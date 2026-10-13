"""The secrets filter on its own: what it catches, and the memories it must not eat.

The store-level tests in `test_memory_store.py` show a refused write; these read the
predicate across the phrasings measured against it. A filter that eats ordinary
memories is worse than one that misses a secret, so every widening of it comes with
the facts it must still let through. Every value here is made up.
"""

from __future__ import annotations

import time

import pytest

from app.kernel.redaction import redact_args
from app.services.memory.sensitive import is_secret_name, is_sensitive, secret_spans


def _seconds(text: str) -> float:
    started = time.perf_counter()
    secret_spans(text)
    return time.perf_counter() - started


@pytest.mark.parametrize(
    ("lead", "unit", "size"),
    [
        ("", "pin 12345 ", 50_000),
        ("", "-----BEGIN PRIVATE KEY-----\n", 200_000),
        ("rtsp://", ":", 32_000),
        ("\u0643\u0644\u0645\u0629 \u0627\u0644\u0633\u0631", " ", 16_000),
        ("", "eyJ-", 64_000),
        ("", "sk-", 32_000),
        ("", "password=", 32_000),
    ],
)
def test_finding_secrets_takes_time_in_proportion_to_the_text(
    lead: str, unit: str, size: int
) -> None:
    """Twice the text, about twice the time. Looking for a line's end from each find took
    5.4 s for 26,000 on one line, and for an END line from each key 1.9 s for 8,000 with
    none; the stream address and the Arabic pattern took 3 s and 8 s on 32,000 colons or
    spaces, and a JWT read the rest of a run from each `-eyJ` (mutations: look from each;
    the patterns as they were; a value or a key read to its end)."""
    small = lead + unit * (size // len(unit))
    large = lead + unit * (2 * size // len(unit))
    small_time = min(_seconds(small) for _ in range(3))
    large_time = min(_seconds(large) for _ in range(3))

    assert large_time <= 3 * max(small_time, 0.002)


@pytest.mark.parametrize(
    "text", ["token:\ntoken:\nabcdefSECRETVALUE", "passwords:\n  pwd:\n    correcthorse"]
)
def test_a_key_word_inside_another_finds_value_is_found_too(text: str) -> None:
    """A value ran onto the next line and took in the key word there, whose own secret,
    a line further on, showed (mutation: look again from where a find ends)."""
    assert secret_spans(text) == [(0, len(text))]


@pytest.mark.parametrize(
    "text",
    ["wifi pass\u00adword: hunter22", "pass\u200bword: hunter22\nnext", "pass\x07word: hunter22"],
)
def test_a_secret_is_found_through_what_renders_as_nothing(text: str) -> None:
    """A soft hyphen inside "password" hid it from every pattern, and a call said the
    rest aloud: blanked from "pass" to the end of its line (mutations: look in the text
    as it came; let ASCII controls through)."""
    line_end = text.find("\n") if "\n" in text else len(text)

    assert is_sensitive(text)
    assert secret_spans(text) == [(text.index("pass"), line_end)]


def test_credentials_in_a_stream_address_and_a_jwt_after_a_hyphen_are_found() -> None:
    """A user name holding an @, and a JWT joined on by a hyphen, slipped past the
    patterns once they were made linear (mutations: stop the user's part at an @; look
    for a JWT only where a run starts)."""
    assert is_sensitive("rtsp://rowan@example.net:hunter22@cam.example.org/stream")
    assert not is_sensitive("rtsp://cam@host:554/stream")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN"
    assert secret_spans(f"callback-{jwt}") == [(9, 9 + len(jwt))]


def test_a_secret_between_blank_letters_is_found() -> None:
    """Hangul fillers render as spaces, and taken out they glued "password is hunter22"
    into one word (mutation: read a blank letter as nothing)."""
    text = "password\u3164is\u3164hunter22"

    assert is_sensitive(text)
    assert secret_spans(text) == [(0, len(text))]


@pytest.mark.parametrize(
    "text",
    [
        "$password = 'x'\nInstall-Update-Task.ps1\nWrite-Host done",
        "PASSWORD=hunter22\nupdate-agent-x64-for-linux",
        "TOKEN=sk-proj-FAKEabcdefghijklmnopqrstu\n./Install-Update-Task.ps1",
        "TOKEN=sk-proj-FAKEabcdefghijklmnopqrstu\n/tmp/.cache/update-agent-x64",
    ],
)
def test_a_command_or_a_path_after_a_secret_stays_shown(text: str) -> None:
    """Only a value cut at the end of its line carries on, onto lines of nothing but its
    characters and no path or option: taken from any line, a command after a secret was
    hidden (mutations: carry on from any line; take a path or an option for a value)."""
    assert secret_spans(text)[0][1] == text.index("\n")


def test_a_value_wrapped_onto_the_next_line_is_blanked_through_it() -> None:
    """A token cut at 64 characters showed its second half. A line with a space, or an
    `=` before its end, carries nothing on (mutations: stop at the find's own line; take
    any `=`; a line break of two characters read as two)."""
    first = "token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIi"
    second = "OiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    for after in ("next line", "LOG_LEVEL=verbose_mode"):
        text = f"{first}\r\n{second}\n{after}"
        assert secret_spans(text) == [(0, len(first) + 2 + len(second))]


def test_a_find_across_a_line_break_runs_to_the_end_of_the_line_it_ends_on() -> None:
    """A `code:` with its number on the next line, after a find on the first (mutation:
    take a line's end for a find that ends past it)."""
    text = "pin 12345 code:\n4455 tail"

    assert secret_spans(text) == [(0, len(text))]


def test_each_private_key_is_blanked_through_its_own_end_line() -> None:
    """And what sits between two keys is not (mutation: the first END line taken for
    every key)."""
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    text = f"{key}\nhello\n{key}\nbye"
    second = text.index(key, 1)

    assert secret_spans(text) == [(0, len(key)), (second, second + len(key))]


@pytest.mark.parametrize(
    "text",
    [
        # Bug 24: "API key" is spoken with a space.
        "My API key is FAKEKEY1234",
        "User's api key is FAKEKEY1234",
        # The extractor paraphrases what it hears, and each of these was one it produced
        # (on another model) after the user's own sentence was caught.
        "User has a password of Hunter2",
        "User has a Wi-Fi password named Hunter2",
        "User's passcode, which is 8891",
        "the safe code was 4455",
        "User set the garage code to 1122",
        "User set the code to 1122",
        # Letters only: no credential shape to catch, only the reworded separator.
        "User's wifi password was bluekettlesunrise",
        "User has a passphrase of correcthorsebattery",
        "User has an api key of sk-liveabc1",
        # The forms that were caught before stay caught.
        "User has a bank PIN of 4821",
        "User's PIN 4821",
        "From now on, remember that the safe code is 4455",
    ],
)
def test_a_secret_is_caught_however_it_is_worded(text: str) -> None:
    assert is_sensitive(text)


@pytest.mark.parametrize(
    "text",
    [
        # Bug 24's other half: a value is never a determiner or a pronoun.
        "the password is the same as before",
        "User changed the password of their router last week",
        "User was a member of a security team",
        "the password reset link is broken",
        "his passphrase manager is Bitwarden",
        # A secret noun then a word that isn't a connective: about something else.
        "User's password manager is 1Password",
        "User is writing the gate code for the B12 allotment",
        "User uses a password manager on the T480 laptop",
        "User forgot their password twice last month",
        "User wants to change the password on the router tonight",
        "User rotates API keys every 90 days",
        "User's API key for the weather service expires in March",
        # Documentation: a parameter described, not a credential given.
        "password: The password to authenticate with.",
        "passwd anonymous 230 Guest login",
    ],
)
def test_an_ordinary_fact_near_a_secret_word_is_kept(text: str) -> None:
    """(mutation: accept any value after a separator, or look past other words)"""
    assert not is_sensitive(text)


@pytest.mark.parametrize(
    "name", ["password", "api_key", "apiKey-token", "client_secret", "Access-Token", "pin"]
)
def test_a_field_named_for_a_secret_is_a_secret_name(name: str) -> None:
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["path", "content", "note", "keyboard", "pinned", "passage"])
def test_an_ordinary_field_name_is_not(name: str) -> None:
    assert not is_secret_name(name)


def test_a_field_named_for_a_secret_is_redacted_whatever_it_holds() -> None:
    """A passphrase can begin with "the", which no longer reads as a value (mutation:
    judge the field by its value alone)."""
    args = redact_args({"password": "the quick brown fox", "note": "the password is the same"})

    assert args == {"password": "[redacted]", "note": "the password is the same"}
