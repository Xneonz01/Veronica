"""What a permission prompt shows of a list, an object or a number: each string, key and
number in it screened on its own, under the name of the field or key that holds it.

Screened as its JSON, a list hid a line break in two characters, so a secret on its
second line showed (found in the review of 0.35.0).
"""

from __future__ import annotations

import json
from dataclasses import replace

from pydantic import BaseModel

from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.display import REDACTED, describe, summarized
from app.kernel.registry import Capability


class _Write(BaseModel):
    path: str
    content: str = ""


async def _noop(_: BaseModel) -> CapabilityResult:
    return CapabilityResult(ok=True)


def _cap(args: type[BaseModel]) -> Capability:
    return Capability(
        name="test.act", description="d", args_model=args, risk=RiskTier.guarded, handler=_noop
    )


class _Many(BaseModel):
    lines: list[str]
    extra: dict[str, str] = {}


class _Code(BaseModel):
    pin: int


class _Amount(BaseModel):
    amount: int
    ids: list[int] = []


class _Nested(BaseModel):
    inner: _Write


def test_each_string_in_a_list_or_an_object_is_screened_on_its_own() -> None:
    """Screened as its JSON, a line break was two characters and the secret after it
    showed; a key named for a secret is blanked whole, in Show all too (mutation: screen
    the JSON)."""
    lines = ["password\n: hunter22", "PIN\n4821"]
    display, full = describe(
        _cap(_Many), {"lines": lines, "extra": {"api_key": "x1", "note": "hi"}}
    )

    shown, extra = display["details"]
    assert shown["value"] == '["[redacted: 19 characters]", "[redacted: 8 characters]"]'
    assert shown["masked"] == 27
    assert extra["value"] == full["extra"] == '{"api_key": "[redacted]", "note": "hi"}'
    assert json.loads(full["lines"]) == lines


def test_a_line_summing_up_a_list_never_shows_its_secret() -> None:
    """(mutation: sum up a list as it came)"""
    capability = replace(_cap(_Many), summarize=lambda a: "send " + "; ".join(a.lines))

    line = summarized(capability, _Many(lines=["hello", "password: hunter22"]))

    assert line == "send hello; [redacted: 18 characters]"


def test_a_field_named_for_a_secret_is_blanked_whatever_its_type() -> None:
    """A number too (mutation: blank only the strings in it)."""
    display, full = describe(_cap(_Code), {"pin": 4821})

    assert display["details"][0]["value"] == full["pin"] == REDACTED


def test_an_argument_screening_leaves_alone_keeps_its_type() -> None:
    """Replaced by its dump, a nested argument would reach the summary as a dict
    (mutation: replace every argument)."""
    capability = replace(_cap(_Nested), summarize=lambda a: f"write {a.inner.path}")

    assert summarized(capability, _Nested(inner=_Write(path="a.txt"))) == "write a.txt"


def test_a_number_that_reads_as_a_secret_is_blanked_and_any_other_stays_a_number() -> None:
    """A card number passed as a number was shown whole once lists were screened item by
    item (mutation: leave numbers alone)."""
    card = 4111111111111111
    display, _ = describe(_cap(_Amount), {"amount": card, "ids": [card, 7]})

    amount, ids = (detail["value"] for detail in display["details"])
    assert amount == "[redacted: 16 characters]"
    assert ids == '["[redacted: 16 characters]", 7]'


def test_inside_an_object_a_path_key_and_the_keys_themselves_are_screened() -> None:
    """Only a top-level path is where the action lands; a key can be a secret, and two
    keys blanked alike stay two (mutations: exempt any key named path; leave keys alone;
    let one blanked key overwrite another)."""
    keys = {"sk-proj-FAKEabcdefghijklmnopqrstu": "x", "sk-proj-FAKEubcdefghijklmnopqrsta": "y"}
    display, _ = describe(_cap(_Many), {"lines": [], "extra": {"path": "token: abcdefSECRET"}})
    shown, _ = describe(_cap(_Many), {"lines": [], "extra": keys})

    assert display["details"][1]["value"] == '{"path": "[redacted: 19 characters]"}'
    assert len(json.loads(shown["details"][1]["value"])) == 2
    assert "FAKE" not in shown["details"][1]["value"]
