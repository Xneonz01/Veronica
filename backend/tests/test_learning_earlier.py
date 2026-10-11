"""A conversation is learned in parts. A part after the first goes with the last turns read
before it, so that "she's vegetarian" after a pause has someone to be, and what the
extractor restates of them, or of what it learned that day, isn't filed (bug 40)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.services.chat.learning_sources import earlier
from app.services.chat.learning_worker import transcript_budget
from app.services.memory import learning as learning_module
from app.services.memory.extraction import EXAMPLE_FACT, extraction_messages, sift
from app.services.memory.fact_words import from_this_part, singular
from app.services.memory.models import ExtractedFact, Extraction, MemoryCreate, MemoryKind, _now
from eval import window
from eval.memory import extraction as memory_eval
from tests.fakes import FakeLLM, ScriptedLLM, build_fake_memory_runtime
from tests.test_call_learning import _runtime
from tests.test_call_learning import _worker as _call_worker
from tests.test_learning_worker import _Extractor, _stores, _worker

Turns = list[dict[str, str]]


def _said(*pairs: tuple[str, str]) -> Turns:
    return [{"role": role, "content": content} for role, content in pairs]


# A conversation from bug 40's measurement, split where a pause split it.
BEFORE = _said(
    ("user", "My sister Morgan is coming to stay next week."),
    ("assistant", "Lovely! How long is she staying?"),
)
PART = _said(
    ("user", "Just the weekend. She's vegetarian, so I need some ideas for dinner."),
    ("assistant", "A mushroom risotto or a vegetable curry would both go down well."),
)
# What qwen3.5:9b filed from PART, shown BEFORE: a restatement, and the part's fact.
_SAID = ("User has a sister named Morgan", "Morgan is vegetarian")
# One whose part says "work" in passing, so a restatement of the job shares a word with
# it; the job was said further back than the turns shown, so only what learning filed
# from it then can hold it back.
SHOWN = _said(("assistant", "What shifts do you do?"))
SHIFTS = _said(("user", "Mostly nights, three a week, which is hard work."))


def _facts(*contents: str) -> list[ExtractedFact]:
    return [ExtractedFact(content=content, confidence=0.95) for content in contents]


@pytest.mark.parametrize(
    ("said", "before", "fact", "anew"),
    [
        # As qwen3.5:9b filed them from this part, shown the turns before it (measured).
        (PART, BEFORE, "Morgan is vegetarian", True),
        (PART, BEFORE, "User has a sister named Morgan", False),
        # Her words in the part aren't the user's (mutation: read her turns there too).
        (
            _said(("user", "She loves my keyboard."), ("assistant", "Cats always do.")),
            _said(("user", "We adopted a tabby cat.")),
            "User has a tabby cat",
            False,
        ),
        # A restatement of what she said before counts too (mutation: the user's only).
        (
            _said(("user", "I cycle in most days.")),
            _said(("user", "I've started a new job."), ("assistant", "At the library, you said?")),
            "User works at a library",
            False,
        ),
        # Filler tells nothing apart (mutation: compare every word).
        (PART, BEFORE, "User's sister is so tired", False),
        # A plural and its singular are one word (mutation: no folding).
        (
            _said(("user", "My hens lay daily.")),
            _said(("user", "I keep a hen.")),
            "User keeps a hen",
            True,
        ),
        # A new fact in the model's own words shares nothing with either (mutation: drop
        # whatever shares no word with the part, as 0.34.11 first did).
        (
            _said(("user", "We just had a little girl!")),
            _said(("user", "We've been trying for a baby.")),
            "User has a daughter",
            True,
        ),
    ],
)
def test_a_fact_that_only_restates_the_turns_before_is_held_back(
    said: Turns, before: Turns, fact: str, anew: bool
) -> None:
    facts = _facts(fact)
    assert (from_this_part(facts, said, before) == facts) is anew


def test_the_turns_before_are_held_against_only_when_they_were_shown() -> None:
    """A first part is filed as before (mutation: hold every part to the rules)."""
    facts = _facts("Morgan is vegetarian", "User has a sister named Morgan")

    shown = sift(facts, PART, [], earlier=BEFORE)
    alone = sift(facts, PART, [], learned=["User has a sister named Morgan"])

    assert [fact.content for fact in shown.facts] == ["Morgan is vegetarian"]
    assert shown.earlier == 1
    assert alone.facts == facts and alone.earlier == 0


def test_a_later_part_doesnt_file_again_what_was_learned_before_it() -> None:
    """A restatement sharing a common word with the part gets past the word rule; what
    learning filed catches it, however far back it was said (the review's leak;
    mutation: no check against what was learned)."""
    facts = _facts("User works as a nurse", "User works nights three times a week")

    sifted = sift(facts, SHIFTS, [], earlier=SHOWN, learned=["User works as a nurse"])
    unchecked = sift(facts, SHIFTS, [], earlier=SHOWN)

    assert [fact.content for fact in sifted.facts] == ["User works nights three times a week"]
    assert sifted.earlier == 1
    assert unchecked.facts == facts  # "work" is in the part


def test_a_later_part_doesnt_file_again_what_the_user_said_before_it() -> None:
    """Learning split the job in two ("User teaches maths", "User works at a secondary
    school"), so neither covers the restatement joining them; the user's own words do,
    "teach" and "teaches" read as one (measured; mutations: no check against their
    words; "teaches" read as "teache")."""
    before = _said(("user", "I teach maths at the secondary school in town."))
    part = _said(("user", "Mostly year nine, and I coach the chess club after school."))
    learned = ["User teaches maths", "User works at a secondary school in town"]
    facts = _facts("User teaches maths at a secondary school", "User coaches the chess club")

    sifted = sift(facts, part, [], earlier=before, learned=learned)

    assert [fact.content for fact in sifted.facts] == ["User coaches the chess club"]
    # Her question isn't something the user said (mutation: hold her words as theirs).
    asked = _said(("assistant", "Do you still cycle to work?"))
    cycles = _facts("User cycles to work")
    assert (
        sift(cycles, _said(("user", "Yes, I cycle every day.")), [], earlier=asked).facts == cycles
    )


@pytest.mark.parametrize(
    ("word", "stem"),
    [("cats", "cat"), ("teaches", "teach"), ("boxes", "box"), ("classes", "class"),
     ("horses", "horse"), ("glass", "glass"), ("bus", "bus")],
)  # fmt: skip
def test_a_plural_or_a_he_form_is_read_as_its_stem(word: str, stem: str) -> None:
    assert singular(word) == stem


def test_sift_counts_what_each_rule_took() -> None:
    facts = _facts(
        EXAMPLE_FACT,
        "User works nights at the hospital",
        "User has a sister named Morgan",
        "Morgan is vegetarian",
    )

    sifted = sift(facts, PART, ["Works nights at the hospital"], earlier=BEFORE)

    assert (sifted.examples, sifted.copies, sifted.earlier) == (1, 1, 1)
    assert [fact.content for fact in sifted.facts] == ["Morgan is vegetarian"]


def test_the_turns_before_a_part_go_with_it_as_context() -> None:
    first = extraction_messages("User: hi")
    after = extraction_messages("User: She's vegetarian.", "User: My sister Morgan is visiting.")

    assert first[1]["content"] == "User: hi"  # a first part goes as it always did
    content = after[1]["content"]
    assert content.index("My sister Morgan") < content.index("She's vegetarian")
    assert "Extract nothing from it" in content
    assert after[0] == first[0]  # the same instruction


def test_the_last_turns_that_fit_a_quarter_of_the_room_go_with_a_part() -> None:
    """Each turn takes its words and a 12-character label (mutations: five turns; the
    whole room; newest last)."""
    read = [("user", "a" * 10), ("assistant", "b" * 10), ("user", "c" * 10)]
    read += [("assistant", "d" * 10), ("user", "e" * 10)]

    assert [turn["content"][0] for turn in earlier(read, 800)] == ["b", "c", "d", "e"]
    assert [turn["content"][0] for turn in earlier(read, 100)] == ["e"]
    assert earlier(read, 40) == [] and earlier([], 400) == []


def test_a_turn_too_long_to_fit_is_left_out_not_cut() -> None:
    """A line cut short can say what it didn't (mutations: stop at it; cut it)."""
    read = [("user", "My sister Morgan is visiting."), ("assistant", "x" * 500)]

    assert earlier(read, 400) == [{"role": "user", "content": "My sister Morgan is visiting."}]


async def test_a_part_after_a_pause_goes_with_the_turns_read_before_it(tmp_path: Path) -> None:
    """(mutations: send no turns before; say they were shown for every part, or never)"""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    for turn in BEFORE:
        await chat.add_message(conversation.id, turn["role"], turn["content"])
    extractor = _Extractor()
    worker = _worker(store, chat, extractor)
    await worker.step()
    await chat.add_message(conversation.id, "user", PART[0]["content"])  # after a pause

    await worker.step()

    assert extractor.read == [BEFORE, PART[:1]]
    assert extractor.earlier == [[], BEFORE]
    assert extractor.shown == [False, True]
    await store.close()


async def test_the_turns_before_are_found_past_a_run_of_tool_rows(tmp_path: Path) -> None:
    """A reply that used a dozen tools leaves a dozen rows after what was said (mutation:
    look through fewer rows)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "My sister Morgan is visiting.")
    for _ in range(12):
        await chat.add_message(conversation.id, "tool", "Nothing found", tool_name="rag_search")
    extractor = _Extractor()
    worker = _worker(store, chat, extractor)
    await worker.step()
    await chat.add_message(conversation.id, "user", "She's vegetarian.")

    await worker.step()

    assert extractor.earlier[1] == [{"role": "user", "content": "My sister Morgan is visiting."}]
    await store.close()


async def test_tool_rows_and_cited_replies_stay_out_of_the_turns_before(tmp_path: Path) -> None:
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "how do I wire the circuit?")
    await chat.add_message(conversation.id, "tool", "Found 1 document", tool_name="rag_search")
    await chat.add_message(
        conversation.id, "assistant", "Connect it carefully [1].", citations=[{"n": 1}]
    )
    extractor = _Extractor()
    worker = _worker(store, chat, extractor)
    await worker.step()
    await chat.add_message(conversation.id, "user", "I have a cat")

    await worker.step()

    assert extractor.earlier[1] == [{"role": "user", "content": "how do I wire the circuit?"}]
    await store.close()


async def test_the_turns_before_a_part_take_their_room_from_it(tmp_path: Path) -> None:
    """The prompt must still fit the model's window (mutation: read the part in full
    beside them). Of 120 characters, the turn before takes 22 and leaves 98: room for
    one 52-character turn, where 120 would take two."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "a" * 10)
    extractor = _Extractor()
    worker = _worker(store, chat, extractor, max_chars=120)
    await worker.step()
    await chat.add_message(conversation.id, "user", "b" * 40)
    await chat.add_message(conversation.id, "user", "c" * 40)

    while await worker.step():
        pass

    assert [[turn["content"][0] for turn in part] for part in extractor.read] == [
        ["a"],
        ["b"],
        ["c"],
    ]
    await store.close()


async def test_a_first_turn_that_needs_the_room_goes_without_the_turns_before(
    tmp_path: Path,
) -> None:
    """Beside the 22 characters before it, a 112-character message would be cut, and a
    turn cut short is never read past its cut: the turns before give way (mutations:
    they never do; say they were shown when they gave way)."""
    store, chat = await _stores(tmp_path)
    conversation = await chat.create("t")
    await chat.add_message(conversation.id, "user", "a" * 10)
    extractor = _Extractor()
    worker = _worker(store, chat, extractor, max_chars=120)
    await worker.step()
    await chat.add_message(conversation.id, "user", "b" * 100)

    await worker.step()

    assert extractor.read[1] == [{"role": "user", "content": "b" * 100}]
    assert extractor.earlier[1] == [] and extractor.shown == [False, False]
    await store.close()


async def test_a_call_read_in_parts_sends_each_later_part_with_the_turns_before(
    tmp_path: Path,
) -> None:
    """Of 120 characters a quarter, 30, holds her 18-character question with its label.
    The part after it has the other 90: room for his 68 but not her 35 after them
    (mutations: calls send no turns before; read a part in full beside them)."""
    mem, chat = await _runtime(tmp_path)
    call = _said(
        ("user", "My brother Sam moved to Glasgow."),
        ("assistant", "Is he settling in?"),
        ("user", "He is a doctor, so he has been busy at the new hospital."),
        ("assistant", "That sounds like a lot."),
    )
    await mem.calls.add(call)
    extractor = _Extractor()
    worker = _call_worker(mem, chat, extractor, max_chars=120)

    while await worker.step():
        pass

    assert extractor.read == [call[:2], call[2:3], call[3:]]
    assert extractor.earlier == [[], call[1:2], call[1:2]]
    assert extractor.shown == [False, True, True]
    await mem.store.close()


async def test_a_calls_first_turn_that_needs_the_room_goes_without_them(tmp_path: Path) -> None:
    """(mutation: calls keep the turns before and cut the turn)"""
    mem, chat = await _runtime(tmp_path)
    call = _said(("user", "a" * 10), ("user", "b" * 100))
    await mem.calls.add(call)
    extractor = _Extractor()
    worker = _call_worker(mem, chat, extractor, max_chars=120)

    while await worker.step():
        pass

    assert extractor.read == [call[:1], call[1:]]
    assert extractor.earlier == [[], []]
    await mem.store.close()


async def test_what_the_extractor_restates_of_the_turns_before_isnt_filed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class _Box:
        def record(self, _level: str, event: str, **fields: object) -> None:
            events.append((event, fields))

    monkeypatch.setattr(learning_module, "current_blackbox", lambda: _Box())
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    seen: list[list[dict[str, str]]] = []
    answer = {"profile": {}, "facts": [{"content": c, "confidence": 0.95} for c in _SAID]}
    llm = FakeLLM([json.dumps(answer)], seen=seen)

    extraction = await mem.learning.extract(PART, llm, "fake-model", BEFORE)
    assert extraction is not None
    await mem.learning.file(extraction, PART, BEFORE)

    assert [memory.content for memory in await mem.service.list()] == ["Morgan is vegetarian"]
    assert ("learning.earlier_dropped", {"count": 1}) in events
    assert BEFORE[0]["content"] in seen[0][1]["content"]  # the request carried them
    await mem.store.close()


async def test_a_memory_learned_that_day_and_undone_stays_undone(tmp_path: Path) -> None:
    """A later part that restates it, in a word of its own, doesn't file it again
    (mutations: hold it against kept memories only; against every part)."""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    undone = await mem.service.add(
        MemoryCreate(kind=MemoryKind.fact, content="User works as a nurse", source="inferred")
    )
    await mem.service.delete(undone.id)  # Undo on the notice, or Delete on the page
    answer = {
        "profile": {},
        "facts": [
            {"content": "User works as a nurse", "confidence": 0.95},
            {"content": "User works nights three times a week", "confidence": 0.95},
        ],
    }
    extraction = await mem.learning.extract(SHIFTS, FakeLLM([json.dumps(answer)]), "m", SHOWN)
    assert extraction is not None

    await mem.learning.file(extraction, SHIFTS, SHOWN)

    active = [memory.content for memory in await mem.service.list()]
    assert active == ["User works nights three times a week"]
    await mem.store.close()


async def test_learning_lists_what_it_filed_lately_whatever_became_of_it(tmp_path: Path) -> None:
    """(mutations: active only; any source; any age; profile slots too)"""
    mem = await build_fake_memory_runtime(tmp_path, InProcessEventBus())
    add = mem.service.add

    kept = await add(MemoryCreate(kind=MemoryKind.fact, content="Likes tea", source="inferred"))
    undone = await add(MemoryCreate(kind=MemoryKind.fact, content="Runs", source="inferred"))
    await mem.service.delete(undone.id)
    waiting = MemoryCreate(kind=MemoryKind.preference, content="Prefers rain", source="inferred")
    await mem.service.add_candidate(waiting)
    await add(MemoryCreate(kind=MemoryKind.fact, content="Asked for this", source="user"))
    old = await add(MemoryCreate(kind=MemoryKind.fact, content="Old news", source="inferred"))
    two_days_ago = (_now() - timedelta(days=2)).isoformat()
    await mem.store.db.execute(
        "UPDATE memories SET created_at=? WHERE id=?", (two_days_ago, old.id)
    )
    await mem.store.db.commit()
    await mem.service.set_profile("occupation", "nurse", source="inferred")

    learned = await mem.store.recent.learned(_now() - timedelta(days=1))

    assert set(learned) == {kept.content, "Runs", "Prefers rain"}
    await mem.store.close()


async def test_the_messages_read_are_the_last_up_to_the_marker(tmp_path: Path) -> None:
    """(mutations: before the marker only; the first ones; any conversation's)"""
    store, chat = await _stores(tmp_path)
    one, other = await chat.create("one"), await chat.create("other")
    ids = [
        await chat.add_message(one.id, "user", "m0"),
        await chat.add_message(one.id, "user", "m1"),
    ]
    await chat.add_message(other.id, "user", "elsewhere")
    ids += [await chat.add_message(one.id, "user", f"m{n}") for n in (2, 3, 4)]

    read = await chat.messages_through(one.id, ids[3], limit=3)

    assert [message.content for message in read] == ["m1", "m2", "m3"]
    await store.close()


def test_the_memory_evaluation_files_a_part_as_learning_does() -> None:
    found = Extraction(facts=_facts(*_SAID))

    shown = memory_eval.filed_active(found, 0.7, 8, PART, earlier=BEFORE)
    learned = memory_eval.filed_active(found, 0.7, 8, PART, earlier=BEFORE, learned=[_SAID[1]])

    assert shown == ["Morgan is vegetarian"] and learned == []
    assert memory_eval.filed_active(found, 0.7, 8, PART) == list(_SAID)


async def test_a_case_read_after_earlier_turns_is_learned_from_first_then_scored() -> None:
    """The part before is read on its own first, as the worker reads it; what it filed
    is held against the part (mutations: send the part without the turns before; hold
    nothing learned against it)."""
    before = {"profile": {}, "facts": [{"content": "User's sister Morgan", "confidence": 0.95}]}
    part = {"profile": {}, "facts": [{"content": c, "confidence": 0.95} for c in _SAID]}
    llm = ScriptedLLM([{"tokens": [json.dumps(before)]}, {"tokens": [json.dumps(part)]}])
    case = {
        "id": "p",
        "read": BEFORE,
        "turns": PART,
        "expected": [{"must": ["vegetarian"], "any_of": [["sister", "morgan"]]}],
        "traps": [],
    }

    result = await memory_eval.run_case(llm, "m", case, 0.7, 8)  # type: ignore[arg-type]

    first, second = llm.calls
    assert (
        BEFORE[0]["content"] in first[1]["content"]
        and PART[0]["content"] not in first[1]["content"]
    )
    assert BEFORE[0]["content"] in second[1]["content"]
    assert result["learned"] == ["User's sister Morgan"]
    assert result["filed"]["filed"] == ["Morgan is vegetarian"] and result["filed"]["met"] == 1


def test_the_largest_learning_prompt_is_a_later_parts() -> None:
    """The window's tripwire holds learning's largest prompt: a part with the turns before
    it and their framing, which a first part lacks (mutation: size a first part)."""
    messages, _ = window.largest(get_settings())["learning"]
    content = messages[1]["content"]

    assert content.startswith("Earlier in this conversation")
    assert len(content) > transcript_budget(get_settings().llm.num_ctx) - 1000


def test_a_gold_line_names_its_claim_in_must_or_any_of_words(tmp_path: Path) -> None:
    def gold(line: dict[str, object]) -> Path:
        path = tmp_path / "gold.jsonl"
        case = {"id": "g", "turns": PART, "expected": [line], "traps": []}
        path.write_text(json.dumps(case) + "\n", encoding="utf-8")
        return path

    assert memory_eval.load_gold(gold({"must": [], "any_of": [["cycle", "bike"]]}))
    # One the loader accepts, the scorer reads (mutation: read `must` as required).
    [case] = memory_eval.load_gold(gold({"any_of": [["cycle", "bike"]]}))
    assert memory_eval.score(case, ["User cycles to work"]).met == 1
    with pytest.raises(ValueError, match="names the words of its claim"):
        memory_eval.load_gold(gold({"must": [], "none_of": ["meat"]}))
