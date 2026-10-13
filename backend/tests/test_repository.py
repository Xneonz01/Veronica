"""Repository guards: what gets published stays anonymous, and private files stay out.

The repository is public and its owner anonymous. These checks run over exactly the
files `git add .` would pick up, honouring every .gitignore, without needing a repo.
Personal terms to look for live in a local, git-ignored file,
`.claude/anonymity-denylist.txt` (one per line), so this test never names them; on a
machine without that file only the generic checks run.
"""

from __future__ import annotations

import os
import re
import unicodedata
from functools import cache
from pathlib import Path

import pathspec
import pytest

REPO = Path(__file__).resolve().parents[2]
DENYLIST = REPO / ".claude" / "anonymity-denylist.txt"

# Placeholder names that docs and tests use on purpose in example paths.
_PLACEHOLDERS = frozenset({"someone", "<user>", "user", "name", "you", "public", "default"})
# An account name is letters, digits, dots, dashes; that also keeps regex source (the
# redactor's own patterns) from reading as a path.
_PROFILE_PATH = re.compile(r"(?i)\b[a-z]:[\\/]+users[\\/]+([a-z0-9._-]+)")
_HOME_PATH = re.compile(r"(?:/home/|/Users/)([A-Za-z0-9._-]+)")
_EMAIL = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+([A-Za-z]{2,}))\b")
_EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")
# `icon@2x.png` looks like an address; a file extension is not a top-level domain.
_FILE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "svg", "ico", "gif", "webp", "json", "js"})


def _spec(folder: Path) -> pathspec.GitIgnoreSpec | None:
    ignore = folder / ".gitignore"
    if not ignore.is_file():
        return None
    return pathspec.GitIgnoreSpec.from_lines(ignore.read_text(encoding="utf-8").splitlines())


@cache
def _published() -> tuple[Path, ...]:
    """The files `git add .` would add: nested .gitignores apply to their own folder."""
    found: list[Path] = []

    def walk(folder: Path, specs: tuple[tuple[Path, pathspec.GitIgnoreSpec], ...]) -> None:
        spec = _spec(folder)
        if spec is not None:
            specs = (*specs, (folder, spec))
        for entry in sorted(os.scandir(folder), key=lambda item: item.name):
            if entry.name == ".git":
                continue
            path = Path(entry.path)
            is_dir = entry.is_dir(follow_symlinks=False)
            ignored = any(
                rules.match_file(path.relative_to(base).as_posix() + ("/" if is_dir else ""))
                for base, rules in specs
            )
            if ignored:
                continue
            if is_dir:
                walk(path, specs)
            else:
                found.append(path)

    walk(REPO, ())
    return tuple(found)


def _texts() -> list[tuple[str, str]]:
    texts = []
    for path in _published():
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary: icons and the like
        texts.append((path.relative_to(REPO).as_posix(), data.decode("utf-8", errors="replace")))
    return texts


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


@pytest.mark.parametrize(
    "private",
    [
        "data/memory/memory.db",
        "data/policy.json",
        "data/voices/clip.wav",
        "backend/data/metrics/voice_turns.jsonl",
        ".env",
        ".env.local",
        "CLAUDE.md",
        ".claude/settings.local.json",
        ".claude/notes/FINDINGS.md",
        "VERONICA_MASTER_PLAN.md",
        "AUDIT_2026-07-28.md",
        "keys/server.pem",
    ],
)
def test_private_files_are_never_published(private: str) -> None:
    spec = _spec(REPO)
    assert spec is not None and spec.match_file(private), f"{private} would be published"


def test_the_example_env_file_is_published() -> None:
    assert REPO / ".env.example" in _published()


def test_published_files_hold_no_machine_paths() -> None:
    offenders = []
    for rel, text in _texts():
        for pattern in (_PROFILE_PATH, _HOME_PATH):
            for match in pattern.finditer(text):
                if match.group(1).lower() not in _PLACEHOLDERS:
                    offenders.append(f"{rel}:{_line(text, match.start())}")
    assert not offenders, f"account paths in published files: {offenders}"


def test_published_files_hold_no_email_addresses() -> None:
    offenders = []
    for rel, text in _texts():
        for match in _EMAIL.finditer(text):
            domain, top_level = match.group(1).lower(), match.group(2).lower()
            if top_level in _FILE_EXTENSIONS or domain.endswith(_EXAMPLE_DOMAINS):
                continue
            offenders.append(f"{rel}:{_line(text, match.start())}")
    assert not offenders, f"email addresses in published files: {offenders}"


def test_published_files_avoid_the_owners_private_terms() -> None:
    if not DENYLIST.is_file():
        pytest.skip("no local denylist on this machine")
    terms = [
        line.strip()
        for line in DENYLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    patterns = [
        re.compile(rf"(?i)(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])") for term in terms
    ]
    offenders = [
        f"{rel}:{_line(text, match.start())}"
        for rel, text in _texts()
        for pattern in patterns
        for match in pattern.finditer(text)
    ]
    # Only locations are reported: printing the matched term would put it in CI logs.
    assert not offenders, f"private terms in published files at: {offenders}"


# Written as themselves, these read as nothing: a bidi override can make code read as
# something other than what it does ("Trojan Source"), and a reviewer can't see a
# zero-width one. Text files may hold them only as escapes.
_SOURCE = (".py", ".ts", ".tsx", ".js", ".css", ".html", ".md", ".yaml", ".toml", ".json")


# Marks and letters that render as nothing too: the Hangul fillers, the grapheme
# joiner, the Khmer inherent vowels, the Mongolian free variation selectors, and
# variation selectors (`core/text_safety.py`).
_BLANK = frozenset("\u115f\u1160\u3164\uffa0\u034f\u17b4\u17b5\u180b\u180c\u180d\u180f")


def _unseen(char: str) -> bool:
    category = unicodedata.category(char)
    return (
        category in ("Cf", "Co", "Cn", "Zl", "Zp")
        or (category == "Cc" and char not in "\t\r\n")
        or char in _BLANK
        or "\ufe00" <= char <= "\ufe0f"
        or "\U000e0100" <= char <= "\U000e01ef"
    )


def test_published_text_holds_no_character_that_renders_as_nothing() -> None:
    """(mutation: write a bidi override into a test as itself)"""
    offenders = [
        f"{rel}:{_line(text, index)} U+{ord(char):04X}"
        for rel, text in _texts()
        if rel.endswith(_SOURCE)
        for index, char in enumerate(text)
        if _unseen(char)
    ]
    assert not offenders, f"write these as escapes: {offenders[:20]}"
