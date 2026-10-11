"""Local file capabilities: read, list and search (safe), and the write (guarded), which
lives in `files_write.py` with the checks it makes before anyone is asked.

All filesystem work runs in a thread, resolving the path included: a directory walk,
or a path on a drive that has gone to sleep, on the event loop would stall audio and
VAD during a live call.
"""

from __future__ import annotations

import asyncio
import codecs
import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from app.capabilities.files_write import register_write
from app.capabilities.paths import PathRefused, resolve_roots, resolve_within
from app.capabilities.protected import Protected
from app.core.config_capabilities import CapabilitySettings
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability, CapabilityRegistry

_PROBE_BYTES = 8192


class ReadArgs(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = Field(description="The file: a full path, or one inside an allowed folder.")


class ListArgs(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = Field(description="The folder: a full path, or one inside an allowed folder.")


class SearchArgs(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = Field(description="Where to search: a full path, or one inside an allowed folder.")
    query: str = Field(description="Text to look for.")
    in_content: bool = Field(default=False, description="Search file contents, not just names.")


# What a read, a listing or a search is asked about: each names one place.
_Located = ReadArgs | ListArgs | SearchArgs


def _refused(exc: PathRefused) -> CapabilityResult:
    return CapabilityResult(ok=False, code=exc.code.value, detail=exc.detail)


def _looks_binary(head: bytes) -> bool:
    """Decide from a fixed-size probe, tolerating a character cut off by the probe.

    A plain `head.decode()` rejects a multi-byte character split at the 8 KB
    boundary, which misclassifies ordinary non-ASCII text as binary. The
    incremental decoder tells "incomplete" apart from "invalid".
    """
    if b"\x00" in head:
        return True
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head, False)  # final=False: a partial tail is not an error
    except UnicodeDecodeError:
        return True
    return False


def register_files(
    registry: CapabilityRegistry, settings: CapabilitySettings, protected: Protected
) -> None:
    roots = resolve_roots(settings.file_roots)

    def _allowed(candidate: str, *, writing: bool) -> Path:
        # Containment first, then Veronica's own files, which no root opens up.
        real = resolve_within(candidate, roots, protected.hides)
        protected.check(real, writing=writing)
        return real

    def _resolved(args: _Located, work: Callable[[Path], CapabilityResult]) -> CapabilityResult:
        try:
            path = _allowed(args.path, writing=False)
        except PathRefused as exc:
            return _refused(exc)
        # Pinned where it resolved: the record then says which file, whichever of her
        # folders a relative path was found in.
        args.path = str(path)
        return work(path)

    def _visible(child: Path) -> bool:
        try:
            # A symlinked child pointing outside the roots is dropped, not listed.
            real = resolve_within(str(child), roots)
        except PathRefused:
            return False
        return not protected.hides(real)

    def _read_sync(path: Path) -> CapabilityResult:
        if not path.is_file():
            return CapabilityResult(ok=False, code=Code.not_found, detail="There's no file there.")
        size = path.stat().st_size
        if size > settings.max_read_bytes:
            return CapabilityResult(
                ok=False, code=Code.file_too_large, detail="That file is too big for me to read."
            )
        with path.open("rb") as handle:
            head = handle.read(_PROBE_BYTES)
            if _looks_binary(head):
                return CapabilityResult(
                    ok=False, code=Code.binary_file, detail="That looks like a binary file."
                )
            rest = handle.read()
        text = (head + rest).decode("utf-8", errors="replace")
        return CapabilityResult(
            ok=True, detail=f"Read {path.name} ({size} bytes).", data={"content": text}
        )

    async def read(args: ReadArgs) -> CapabilityResult:
        return await asyncio.to_thread(_resolved, args, _read_sync)

    def _list_sync(path: Path) -> CapabilityResult:
        if not path.is_dir():
            return CapabilityResult(
                ok=False, code=Code.not_found, detail="There's no folder there."
            )
        entries: list[dict[str, object]] = []
        # Filtered before counting, so the total says nothing about what was hidden.
        children = [child for child in sorted(path.iterdir()) if _visible(child)]
        # Mark a cut-off listing, or the model will present a partial list as
        # everything in the folder.
        truncated = len(children) > settings.search_max_results
        for child in children[: settings.search_max_results]:
            entries.append(
                {"name": child.name, "is_dir": child.is_dir(), "size": _safe_size(child)}
            )
        suffix = f" (first {len(entries)} of {len(children)})" if truncated else ""
        return CapabilityResult(
            ok=True,
            detail=f"{len(entries)} item(s) in {path.name}{suffix}.",
            data={"entries": entries, "truncated": truncated, "total": len(children)},
        )

    async def list_dir(args: ListArgs) -> CapabilityResult:
        return await asyncio.to_thread(_resolved, args, _list_sync)

    def _search_sync(root: Path, query: str, in_content: bool) -> CapabilityResult:
        deadline = time.monotonic() + settings.search_budget_s
        needle = query.lower()
        hits: list[dict[str, object]] = []
        truncated = False
        for current, dirs, names in os.walk(root):
            if time.monotonic() > deadline or len(hits) >= settings.search_max_results:
                truncated = True
                break
            # Prune in place so the walk never descends into Veronica's own data.
            dirs[:] = [name for name in dirs if _visible(Path(current) / name)]
            for name in names:
                # Check the deadline per file, not per directory: one huge folder could
                # otherwise run far past the budget, and a worker thread can't be
                # cancelled, so it would keep reading after the kernel gave up.
                if time.monotonic() > deadline:
                    truncated = True
                    break
                candidate = Path(current) / name
                if not _visible(candidate):
                    continue
                if needle in name.lower():
                    hits.append({"path": str(candidate), "line": 0, "excerpt": name})
                elif in_content:
                    hit = _content_hit(candidate, needle, settings.max_read_bytes)
                    if hit is not None:
                        hits.append(hit)
                if len(hits) >= settings.search_max_results:
                    truncated = True
                    break
        return CapabilityResult(
            ok=True,
            detail=f"{len(hits)} match(es){' (partial)' if truncated else ''}.",
            data={"hits": hits, "truncated": truncated},
        )

    async def search(args: SearchArgs) -> CapabilityResult:
        def walk(root: Path) -> CapabilityResult:
            return _search_sync(root, args.query, args.in_content)

        return await asyncio.to_thread(_resolved, args, walk)

    registry.register(
        Capability(
            name="files.read",
            voice_exposed=True,
            description="Read a text file the user has allowed.",
            args_model=ReadArgs,
            risk=RiskTier.safe,
            handler=read,
            summarize=lambda a: f"read {a.path}",
        )
    )
    registry.register(
        Capability(
            name="files.list",
            voice_exposed=True,
            # Her folders, named once for every file tool: the model sees them all
            # together, and, told nothing, it guessed at paths (bug 29).
            description=f"List the files in a folder. The allowed folders: {_named(roots)}.",
            args_model=ListArgs,
            risk=RiskTier.safe,
            handler=list_dir,
            summarize=lambda a: f"list {a.path}",
        )
    )
    registry.register(
        Capability(
            name="files.search",
            voice_exposed=True,
            description="Find files by name, or by their contents.",
            args_model=SearchArgs,
            risk=RiskTier.safe,
            handler=search,
            timeout_s=settings.search_budget_s + 5.0,
            summarize=lambda a: f"search {a.path} for {a.query!r}",
        )
    )
    register_write(registry, settings, lambda path: _allowed(path, writing=True))


def _named(roots: list[Path]) -> str:
    return ", ".join(str(root) for root in roots) or "none yet"


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _content_hit(path: Path, needle: str, max_bytes: int) -> dict[str, object] | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        with path.open("rb") as handle:
            head = handle.read(_PROBE_BYTES)
            if _looks_binary(head):
                return None
            rest = handle.read()
    except OSError:
        return None
    text = (head + rest).decode("utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line.lower():
            return {"path": str(path), "line": number, "excerpt": line.strip()[:160]}
    return None
