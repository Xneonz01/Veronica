"""The secrets filter on its own: what it catches, and the memories it must not eat.

The store-level tests in `test_memory_store.py` show a refused write; these read the
predicate across the phrasings measured against it. A filter that eats ordinary
memories is worse than one that misses a secret, so every widening of it comes with
the facts it must still let through. Every value here is made up.
"""

from __future__ import annotations

import pytest

from app.kernel.redaction import redact_args
from app.services.memory.sensitive import is_secret_name, is_sensitive


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
