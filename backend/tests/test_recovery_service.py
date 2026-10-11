"""Making and using her recovery key (`services/recovery.py`, `api/routes_recovery.py`):
every data key sealed or none, shown once and recorded, and a start locked to another
account opened with it, the notice then saying to restart. Real DPAPI: these run on
Windows."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import data_acl, dpapi, recovery_key
from app.core.config import Settings
from app.core.data_key import KEY_BYTES, open_key, seal_recovery
from app.core.problems import Problem
from app.core.recovery_key import NotARecoveryKey
from app.core.sealed import seal
from app.main import create_app
from app.schemas.events import Event
from app.services.recovery import NoneMade, RecoveryKeys, Unavailable, Used, Wrong

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows service")
APP = {"Origin": "http://localhost:5173"}


def _keys(folder: Path) -> tuple[RecoveryKeys, Path, Path]:
    memory, voices = folder / "memory" / "memory.key", folder / "voices" / "voices.key"
    return RecoveryKeys({"memory": memory, "voices": voices}), memory, voices


def _made(path: Path) -> bytes:
    key = open_key(path, encrypted=False)
    assert key is not None
    return key


def _as_another_account(path: Path) -> None:
    """The key file as another Windows account would hold it: its recovery sealing as it
    was, its wrapping one this account can't unwrap."""
    stored = json.loads(path.read_text(encoding="utf-8"))
    key = open_key(path, encrypted=True)
    assert key is not None
    blob = dpapi.protect(key, b"another account's")
    stored["dpapi"] = base64.b64encode(blob).decode("ascii")
    path.write_text(json.dumps(stored), encoding="utf-8")


def test_a_recovery_key_seals_every_data_key_and_opens_them(tmp_path: Path) -> None:
    keys, memory, voices = _keys(tmp_path)
    opened = {_made(memory), _made(voices)}

    written, made = keys.make()

    status = keys.status()
    assert (status.made, status.partial, status.unavailable) == (made, False, None)
    used = keys.use(written)
    assert (used.restored, used.opened, used.unmatched) == ([], ["memory", "voices"], [])
    assert {open_key(memory, encrypted=True), open_key(voices, encrypted=True)} == opened


def test_none_is_sealed_unless_every_key_opens(tmp_path: Path) -> None:
    """Else one key file would keep an older recovery key than the other (mutation: seal
    each as it opens)."""
    keys, memory, voices = _keys(tmp_path)
    _made(memory)
    _made(voices)
    _as_another_account(voices)
    before = memory.read_bytes()

    with pytest.raises(Unavailable) as refused:
        keys.make()

    assert refused.value.reason == "locked" and memory.read_bytes() == before
    assert keys.status().unavailable == "locked"


def test_a_new_recovery_key_replaces_the_one_before(tmp_path: Path) -> None:
    keys, memory, voices = _keys(tmp_path)
    _made(memory)
    _made(voices)
    first, _ = keys.make()
    second, _ = keys.make()

    with pytest.raises(Wrong):
        keys.use(first)
    assert keys.use(second).opened == ["memory", "voices"]


def test_it_opens_for_this_account_what_another_account_wrapped(tmp_path: Path) -> None:
    keys, memory, voices = _keys(tmp_path)
    opened = (_made(memory), _made(voices))
    written, made = keys.make()
    _as_another_account(memory)
    _as_another_account(voices)

    used = keys.use(written)

    assert used.restored == ["memory", "voices"]
    assert (open_key(memory, encrypted=True), open_key(voices, encrypted=True)) == opened
    assert keys.status().made == made


def test_a_key_made_since_is_said_as_not_covered(tmp_path: Path) -> None:
    """Her voices' key made again after the recovery key: it can't open them (mutation:
    judge by one key file)."""
    keys, memory, voices = _keys(tmp_path)
    _made(memory)
    written, made = keys.make()
    _made(voices)

    status = keys.status()
    assert (status.made, status.partial) == (None, True)
    used = keys.use(written)
    assert (used.opened, used.unmatched) == (["memory"], ["voices"])
    assert made


def test_with_no_sealing_anywhere_it_says_none_was_made(tmp_path: Path) -> None:
    keys, memory, voices = _keys(tmp_path)
    _made(memory)
    _made(voices)

    with pytest.raises(NoneMade):
        keys.use(recovery_key.written(recovery_key.new()))
    with pytest.raises(NotARecoveryKey):
        keys.use("not a key")


def test_where_there_is_no_dpapi_none_is_made_or_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys, memory, voices = _keys(tmp_path)
    _made(memory)
    monkeypatch.setattr(dpapi, "available", lambda: False)

    assert keys.status().unavailable == "unsupported"
    for attempt in (keys.make, lambda: keys.use(recovery_key.written(bytes(20)))):
        with pytest.raises(Unavailable) as refused:
            attempt()
        assert refused.value.reason == "unsupported"


def test_the_recovery_key_is_never_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    keys, memory, voices = _keys(tmp_path)
    _made(memory)
    _made(voices)

    with caplog.at_level(logging.DEBUG):
        written, _ = keys.make()
        keys.use(written)

    compact = written.replace("-", "")
    assert written not in caplog.text and compact not in caplog.text


# -- in the app --


def _app(data: Path, as_set_up: pytest.MonkeyPatch) -> TestClient:
    async def keep_private(_path: Path) -> data_acl.Kept:
        return data_acl.Kept("ok")

    as_set_up.setattr(data_acl, "keep_private", keep_private)
    return TestClient(create_app(Settings(data_dir=str(data))), base_url="http://localhost")


def _problems(client: TestClient) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = client.get("/health").json()["problems"]
    return problems


def test_made_in_the_app_it_is_shown_once_and_recorded(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Never cached, and the activity log says it was made, never what it is."""
    with _app(tmp_path / "data", as_set_up) as client:
        made = client.post("/recovery-key", headers=APP)
        status = client.get("/recovery-key").json()
        used = client.post("/recovery-key/use", json={"key": made.json()["key"]}, headers=APP)
        rows = client.get("/audit").json()["rows"]
        too_long = client.post("/recovery-key/use", json={"key": "0" * 201}, headers=APP)
        wrong = client.post(
            "/recovery-key/use", json={"key": recovery_key.written(recovery_key.new())}
        )
        typo = client.post("/recovery-key/use", json={"key": "0000-0000"})

    assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
    written = made.json()["key"]
    assert recovery_key.read(written) and status["made"] == made.json()["made"]
    logged = [(row["capability"], row["actor"], row["confirm_via"]) for row in rows]
    assert ("recovery_key.made", "user", "ui") in logged
    assert ("recovery_key.used", "user", "ui") in logged
    assert too_long.status_code == 422
    assert written not in json.dumps(rows)
    assert used.json() == {"restored": [], "opened": ["memory", "voices"], "unmatched": []}
    assert (wrong.status_code, wrong.json()["code"]) == (422, "recovery_key_wrong")
    assert (typo.status_code, typo.json()["code"]) == (422, "recovery_key_invalid")


def test_a_start_locked_to_another_account_is_opened_with_it(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Said as one a recovery key can open; opened, the notices give way to one that
    says to restart, and the restart opens her memory and voices (mutations: say
    nothing of it; leave the notices)."""
    data = tmp_path / "data"
    with _app(data, as_set_up) as client:
        written = client.post("/recovery-key").json()["key"]
    voices_key = data / "voices" / "voices.key"
    key = open_key(voices_key, encrypted=True)
    assert key is not None and len(key) == KEY_BYTES
    (data / "voices" / "rowan.wav").write_bytes(seal(b"RIFF clip", key, "rowan.wav"))
    _as_another_account(data / "memory" / "memory.key")
    _as_another_account(voices_key)

    with _app(data, as_set_up) as client:
        locked = _problems(client)
        used = client.post("/recovery-key/use", json={"key": written})
        after = _problems(client)

    assert {
        "code": "memory_locked",
        "detail": {"reason": "another_account", "recoverable": 1},
    } in locked
    assert {
        "code": "voices_locked",
        "detail": {"reason": "another_account", "recoverable": 1},
    } in locked
    assert used.json()["restored"] == ["memory", "voices"]
    assert after == [{"code": "data_recovered", "detail": {"memory": 1, "voices": 1}}]

    with _app(data, as_set_up) as client:
        assert _problems(client) == []
        assert client.get("/memory").status_code == 200


def test_a_recovery_sealing_on_a_file_made_again_is_left_out(tmp_path: Path) -> None:
    """A key file set aside and made again carries no sealing: only the ones sealed are
    opened (the status says the rest isn't covered)."""
    keys, memory, voices = _keys(tmp_path)
    key = _made(memory)
    _made(voices)
    sealing = recovery_key.sealing_key(secret := os.urandom(recovery_key.SECRET_BYTES))
    seal_recovery({memory: key}, sealing, recovery_key.key_id(secret), "2026-09-26T12:00:00+00:00")

    used = keys.use(recovery_key.written(secret))

    assert (used.opened, used.unmatched) == (["memory"], ["voices"])


def test_two_recovery_keys_made_in_one_second_are_told_apart(tmp_path: Path) -> None:
    """Judged by the time alone, two made in the same second read as one covering both
    (mutation: compare when they were made)."""
    keys, memory, voices = _keys(tmp_path)
    made = "2026-09-26T12:00:00+00:00"
    for path in (memory, voices):
        secret = recovery_key.new()
        seal_recovery(
            {path: _made(path)}, recovery_key.sealing_key(secret), recovery_key.key_id(secret), made
        )

    status = keys.status()

    assert (status.made, status.partial) == (None, True)


def test_none_is_made_while_the_start_found_her_data_locked(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """A key file that unwraps but opens nothing (key_mismatch), or one missing: its
    sealing would be a recovery key that opens nothing, and Settings said it covered
    everything (mutation: judge by the key files alone)."""
    with _app(tmp_path / "data", as_set_up) as client:
        problems: list[Problem] = client.app.state.problems  # type: ignore[attr-defined]
        problems.append(Problem(code="memory_locked", detail={"reason": "key_mismatch"}))
        status = client.get("/recovery-key").json()
        made = client.post("/recovery-key", headers=APP)

    assert status["unavailable"] == "locked"
    assert (made.status_code, made.json()["code"]) == (409, "recovery_locked")


def test_a_key_made_by_another_program_is_said_as_that(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """It replaces the key the person saved: her activity log says who asked, and every
    window hears of it with the time (mutations: always the person; no word of it)."""
    heard: list[dict[str, Any]] = []

    async def hear(event: Event) -> None:
        heard.append(event.payload)

    with _app(tmp_path / "data", as_set_up) as client:
        client.app.state.event_bus.subscribe("recovery.updated", hear)  # type: ignore[attr-defined]
        by_app = client.post("/recovery-key", headers=APP).json()
        by_program = client.post("/recovery-key").json()
        rows = client.get("/audit").json()["rows"]

    made = [
        (row["actor"], row["confirm_via"])
        for row in rows
        if row["capability"] == "recovery_key.made"
    ]
    assert sorted(made) == [("program", "api"), ("user", "ui")]
    assert {"via": "ui", "made": by_app["made"]} in heard
    assert {"via": "api", "made": by_program["made"]} in heard


def test_a_restore_is_announced_and_later_ones_join_it(
    tmp_path: Path, as_set_up: pytest.MonkeyPatch
) -> None:
    """Every surface reads the problems again; a second restore adds to the notice
    (mutations: no announcement; the second replaces the first)."""
    from app.api import routes_recovery

    changed: list[str] = []

    async def hear(event: Event) -> None:
        changed.append(event.type)

    with _app(tmp_path / "data", as_set_up) as client:
        app = client.app
        app.state.event_bus.subscribe("problems.changed", hear)  # type: ignore[attr-defined]
        problems: list[Problem] = app.state.problems  # type: ignore[attr-defined]
        problems[:] = [
            Problem(code="memory_locked", detail={"reason": "another_account"}),
            Problem(code="voices_locked", detail={"reason": "another_account"}),
        ]

        class _Request:
            def __init__(self) -> None:
                self.app = app

        client.portal.call(routes_recovery._recovered, _Request(), Used(restored=["memory"]))  # type: ignore[union-attr]
        client.portal.call(routes_recovery._recovered, _Request(), Used(restored=["voices"]))  # type: ignore[union-attr]
        after = [problem.model_dump() for problem in problems]

    assert after == [{"code": "data_recovered", "detail": {"memory": 1, "voices": 1}}]
    assert changed == ["problems.changed", "problems.changed"]
