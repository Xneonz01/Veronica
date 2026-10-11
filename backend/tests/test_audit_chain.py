"""The audit log's hash chain on its own: rows in, the first one that doesn't add up out."""

from __future__ import annotations

from typing import Any

import pytest

from app.kernel.audit_chain import GENESIS, Head, chain_hash, verify


def _row(row_id: int, prev_hash: str | None, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": row_id,
        "ts": f"2026-09-25T10:00:0{row_id}+00:00",
        "capability": "memory.save",
        "actor": "veronica:chat",
        "decision": "allowed",
        "ok": 1,
        "code": "",
        "detail": "Saved: User takes their coffee black",
        "args_json": '{"content": "User takes their coffee black"}',
        "duration_ms": 12,
        "confirm_via": None,
        "prev_hash": prev_hash,
    }
    row.update(changes)
    row["hash"] = chain_hash(row) if prev_hash is not None else None
    return row


def _chain(count: int, *, unchained: int = 0) -> list[dict[str, Any]]:
    rows = [_row(i, None) for i in range(1, unchained + 1)]
    previous = GENESIS
    for i in range(unchained + 1, unchained + count + 1):
        row = _row(i, previous)
        rows.append(row)
        previous = row["hash"]
    return rows


def _head(rows: list[dict[str, Any]]) -> Head | None:
    """Where the store would have left the chain after writing `rows`."""
    chained = [row for row in rows if row["hash"] is not None]
    return Head(chained[-1]["id"], chained[-1]["hash"]) if chained else None


def test_an_untouched_chain_walks_clean() -> None:
    rows = _chain(5)
    report = verify(rows, _head(rows))
    assert report.ok and report.rows == 5 and report.unchained == 0


def test_an_edited_row_is_found() -> None:
    rows = _chain(5)
    head = _head(rows)
    rows[2]["detail"] = "Saved: User is an administrator"  # edited, hash left alone

    report = verify(rows, head)

    assert not report.ok and report.broken_at == 3


def test_a_deleted_row_breaks_the_link_after_it() -> None:
    rows = _chain(5)
    head = _head(rows)
    del rows[1]

    assert verify(rows, head).broken_at == 3


def test_an_inserted_row_is_found() -> None:
    rows = _chain(4)
    head = _head(rows)
    forged = _row(99, "not the previous hash")
    rows.insert(2, forged)

    assert verify(rows, head).broken_at == 99


def test_rows_from_before_the_chain_are_counted_not_verified() -> None:
    rows = _chain(3, unchained=4)
    report = verify(rows, _head(rows))
    assert report.ok and report.unchained == 4 and report.rows == 7


def test_a_row_without_a_hash_after_the_chain_began_is_a_break() -> None:
    """Nothing this build writes lacks a hash, so one appearing mid-chain was put there
    by something else (mutation: count it as from before the chain)."""
    rows = _chain(3)
    head = _head(rows)
    rows.append({**_row(4, None), "prev_hash": None})

    assert verify(rows, head).broken_at == 4


def test_a_hash_recomputed_over_an_edit_still_breaks_the_next_link() -> None:
    """Rewriting one row and its own hash isn't enough: the row after it names the
    old hash. Only rewriting every hash after it walks clean, which the chain can't
    stop without a key (ARCHITECTURE says so)."""
    rows = _chain(4)
    head = _head(rows)
    edited = _row(2, rows[1]["prev_hash"], detail="Saved: something else")
    rows[1] = edited

    assert verify(rows, head).broken_at == 3


def test_removing_the_newest_rows_is_found() -> None:
    """No link names a row after it, so only the head sees them go (mutation: skip the
    comparison with the head)."""
    rows = _chain(5)
    head = _head(rows)
    del rows[3:]

    assert verify(rows, head).broken_at == 5


def test_an_emptied_log_is_found() -> None:
    assert verify([], _head(_chain(3))).broken_at == 3


def test_blanked_hashes_are_found() -> None:
    """With every hash gone the rows would pass as from before the chain."""
    rows = _chain(3)
    head = _head(rows)
    for row in rows:
        row["hash"] = row["prev_hash"] = None
    rows[1]["detail"] = "Saved: something else"

    report = verify(rows, head)

    assert report.broken_at == 3 and report.unchained == 3


def test_a_row_past_the_head_is_one_the_store_never_wrote() -> None:
    """Even one that links and recomputes (mutation: drop the id check)."""
    rows = _chain(4)

    assert verify(rows, _head(rows[:3])).broken_at == 4


def test_a_chain_with_no_head_is_a_break() -> None:
    """The store writes the head with every row, and gives a chain from before the head
    one when it creates the table; a chain without one had it removed."""
    rows = _chain(2, unchained=1)

    assert verify(rows, None).broken_at == 2
    assert verify(_chain(0, unchained=2), None).ok


def test_text_read_as_bytes_walks_as_text() -> None:
    """The store reads text columns as bytes, so one that isn't UTF-8 can't fail the read
    (mutation: leave them undecoded)."""
    rows = _chain(3)
    head = _head(rows)
    as_read = [
        {name: value.encode() if isinstance(value, str) else value for name, value in row.items()}
        for row in rows
    ]

    assert verify(as_read, head).ok


def test_every_row_is_counted_past_a_break() -> None:
    rows = _chain(5)
    head = _head(rows)
    rows[1]["detail"] = "Saved: something else"

    report = verify(rows, head)

    assert report.broken_at == 2 and report.rows == 5


@pytest.mark.parametrize("change", [{"detail": b"\x00\xff"}, {"duration_ms": float("inf")}])
def test_a_value_no_row_of_ours_holds_is_a_break_not_a_crash(change: dict[str, Any]) -> None:
    """Bytes or an infinity in a hashed column raised out of the walk (mutation: let
    the error through)."""
    rows = _chain(3)
    head = _head(rows)
    rows[1].update(change)

    assert verify(rows, head).broken_at == 2
