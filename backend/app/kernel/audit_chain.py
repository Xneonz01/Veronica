"""The audit log's hash chain: how a row is digested, and how the chain is walked.

The triggers on `capability_audit` stop this application from rewriting history, and
nothing else: anyone with a SQLite shell can drop them and edit a row. So each row also
carries `hash = sha256(prev_hash + canonical JSON of the row, prev_hash included)`,
where `prev_hash` is the row before's `hash`. An edited row no longer recomputes, and
a deleted or inserted one breaks the link after it, so the walk finds the first row
that doesn't add up.

**What it proves.** The digest has no key: someone who rewrites a row and every hash
after it produces a chain that walks clean. It catches an edit made without
recomputing the rest, by hand or by a tool that doesn't know the rule, which is the
realistic case for a log on the user's own disk; it is not a signature.

**Where it ends.** Deleting the newest rows breaks no link, so the store also keeps the
chain's head (the newest row's id and hash, `capability_audit_head`), written in the
same transaction as the row, and the walk must end exactly there. Rows removed from the
end, an emptied log and blanked hashes all miss the head, and a row appended after a
removal links to the head's hash, so the break stays. Like the chain, the head has no
key: someone who knows the rule rewrites it too.

**Rows from before the chain.** Rows written before the chain existed have no hash.
They are counted, not verified, and the chain begins at the first row that has one,
linked to `GENESIS`. A row without a hash after that is a break: nothing this build
writes lacks one.

**Reviewed breaks.** A break the person has looked at can be accepted (`review`, from
`POST /audit/review`): each row that doesn't add up is anchored by a digest of the
row exactly as stored and of the link before it, and the head moves to the chain's end.
The walk takes an anchored row as it was reviewed, where the chain before it is too,
and passes its hash on (or, with none it can read, its anchor), so the rows after it
are judged as before. A row changed, or one before it removed, after the review breaks
again. The anchors have no key either.

**The digest covers exactly `HASHED_COLUMNS`,** named once so the writer and the walk
cannot drift. Adding a column without adding it here leaves it unprotected; adding it
here changes every digest, so it would need a version on the row to tell old rows from
new, which the table doesn't carry until it needs one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol

GENESIS = "veronica-audit-chain-1"

HASHED_COLUMNS = (
    "ts",
    "capability",
    "actor",
    "decision",
    "ok",
    "code",
    "detail",
    "args_json",
    "duration_ms",
    "confirm_via",
    "prev_hash",
)
# The columns that hold text. The store reads them as bytes, and the walk decodes them,
# so a value that isn't UTF-8 (only an edit from outside writes one) is a broken row.
TEXT_COLUMNS = frozenset(HASHED_COLUMNS) - {"ok", "duration_ms"} | {"hash"}


class Row(Protocol):
    """A row by column name: a dict, or the `sqlite3.Row` the walk is handed as read."""

    def __getitem__(self, key: str, /) -> Any: ...


def canonical_json(row: Row) -> bytes:
    """The one serialisation the digest is taken over."""
    body = {name: row[name] for name in HASHED_COLUMNS}
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def chain_hash(row: Row) -> str:
    """The row's digest, over its own `prev_hash` and the columns above."""
    return hashlib.sha256(str(row["prev_hash"]).encode("ascii") + canonical_json(row)).hexdigest()


@dataclass(frozen=True, slots=True)
class Head:
    """Where the store last left the chain: its newest row's id and hash."""

    last_id: int
    hash: str


@dataclass(frozen=True, slots=True)
class ChainReport:
    """One walk of the log: how many rows, how many predate the chain, the first bad one."""

    rows: int
    unchained: int = 0
    broken_at: int | None = None

    @property
    def ok(self) -> bool:
        return self.broken_at is None


@dataclass(frozen=True, slots=True)
class Review:
    """What accepts the log as it is: the anchors, by row id, for every row taken as
    reviewed, those it newly anchored, and the head at the chain's end. `whole`: there
    was nothing to accept, as `verify` would have found."""

    anchors: dict[int, str]
    new: tuple[int, ...]
    head: Head
    whole: bool


def verify(
    rows: Iterable[Row], head: Head | None, anchors: Mapping[int, str] | None = None
) -> ChainReport:
    """Walk `rows` in id order: each must link to the one before and recompute, and the
    chain must end at `head` (None: the store has never written a chained row). A row
    with an anchor is taken as it was reviewed, if it hasn't changed since. `broken_at`
    is the first row that doesn't add up, or the head's id when the rows after some
    point are gone; `rows` counts them all. Synchronous and CPU-bound: run it off the
    event loop."""
    count = unchained = 0
    expected: str | None = None
    broken: int | None = None
    for raw in rows:
        count += 1
        if broken is not None:
            continue
        verdict, expected = _judge(raw, expected, head, anchors or {})
        if verdict is _Verdict.broken:
            broken = int(raw["id"])
        elif verdict is _Verdict.unchained:
            unchained += 1
    end = expected if expected is not None else GENESIS  # a head before any row names it
    if broken is None and head is not None and end != head.hash:
        broken = head.last_id  # its newest rows were removed
    return ChainReport(count, unchained, broken)


def review(rows: Iterable[Row], head: Head | None, anchors: Mapping[int, str]) -> Review:
    """Accept the log as it is: each row that doesn't add up is anchored as stored (one
    past `head` too), an anchor that still matches is kept, and the head is the chain's
    end, wherever the old one was. `verify` with the result finds nothing. One walk, as
    `verify`'s, judged the same way but on past each break."""
    kept: dict[int, str] = {}
    new: list[int] = []
    expected: str | None = None
    last_id = 0
    for raw in rows:
        verdict, value = _judge(raw, expected, head, anchors)
        if verdict is _Verdict.unchained:
            continue
        row_id = int(raw["id"])
        if verdict is _Verdict.broken:
            kept[row_id] = anchor_for(raw, expected)
            new.append(row_id)
            value = _passed_on(raw, kept[row_id])
        elif verdict is _Verdict.anchored:
            kept[row_id] = anchors[row_id]
        expected, last_id = value, row_id
    end = Head(last_id, expected) if expected is not None else Head(0, GENESIS)
    whole = not new and (head is None or end.hash == head.hash)
    return Review(kept, tuple(new), end, whole)


def anchor_for(row: Row, before: str | None) -> str:
    """What a review keeps of `row`: a digest of it exactly as stored, its id and hash
    included, and of the link before it (`before`, None ahead of the chain). A later
    change to the row, or to the chain before it, then shows again. Taken of rows that
    don't add up, so of any value: text or bytes alike, and kinds no row of ours holds."""
    values = [_tagged(row[name]) for name in ("id", *HASHED_COLUMNS, "hash")]
    values.append("" if before is None else _tagged(before))
    return hashlib.sha256(json.dumps(values).encode("ascii")).hexdigest()


class _Verdict(Enum):
    linked = auto()  # links to the row before and recomputes
    anchored = auto()  # as it was when reviewed
    unchained = auto()  # from before the chain
    broken = auto()


def _judge(
    raw: Row, expected: str | None, head: Head | None, anchors: Mapping[int, str]
) -> tuple[_Verdict, str | None]:
    """What `raw` is, after a chain whose last link is `expected` (None: not begun),
    and the link the next row must name."""
    row_id = int(raw["id"])
    anchor = anchors.get(row_id)
    if anchor is not None:
        if anchor_for(raw, expected) == anchor:
            return _Verdict.anchored, _passed_on(raw, anchor)
        return _Verdict.broken, expected
    row = _decoded(raw)
    if row is None:
        return _Verdict.broken, expected
    if row["hash"] is None:
        # Once the chain has begun, nothing this build writes lacks one.
        return (_Verdict.unchained if expected is None else _Verdict.broken), expected
    # A row past the head is one the store never wrote.
    if head is None or row_id > head.last_id or not _adds_up(row, expected):
        return _Verdict.broken, expected
    return _Verdict.linked, str(row["hash"])


def _passed_on(raw: Row, anchor: str) -> str:
    """The link after a row taken as reviewed: its hash, or its anchor when it has none
    that reads as text (the head a review leaves names the same)."""
    stored = raw["hash"]
    if isinstance(stored, bytes):
        try:
            return stored.decode("utf-8")
        except UnicodeDecodeError:
            return anchor
    return stored if isinstance(stored, str) else anchor


def _tagged(value: object) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8", "surrogatepass")
    if isinstance(value, bytes):
        return "b" + value.hex()
    return f"{type(value).__name__}:{value!r}"


def _decoded(row: Row) -> dict[str, Any] | None:
    """`row` with its text decoded, or None when some of it isn't UTF-8."""
    out: dict[str, Any] = {"id": row["id"]}
    for name in (*HASHED_COLUMNS, "hash"):
        value = row[name]
        if isinstance(value, bytes) and name in TEXT_COLUMNS:
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return None
        out[name] = value
    return out


def _adds_up(row: Row, expected: str | None) -> bool:
    if row["prev_hash"] != (expected if expected is not None else GENESIS):
        return False
    stored: object = row["hash"]
    try:
        return chain_hash(row) == stored
    except (TypeError, ValueError):  # a value no row of ours holds: bytes, an infinity
        return False
