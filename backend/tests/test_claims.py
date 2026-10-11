"""The claim check on its own: which sentences say she did something, and what backs them.

The first rows are replies the model gave in browser runs when no tool had run; the
rest are sentences a row must not fire on, each for the reason its row is narrow. Two
reviews added most of them: ordinary answers that got a false note, and real claims
that got none.
"""

from __future__ import annotations

import time

import pytest

from app.capabilities import register_capabilities
from app.core.config import get_settings
from app.kernel.capability import RiskTier
from app.kernel.registry import CapabilityRegistry
from app.services.tools.claims import BACKED_BY, CAVEAT, FAMILY_OF, caveats, unsupported
from app.services.tools.claims_table import claimed

APPS = ("notepad", "explorer")


def _kinds(reply: str, apps: tuple[str, ...] = APPS) -> list[tuple[str, ...]]:
    return [claim.kinds for claim in claimed(reply, apps)]


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ("I'll update that memory — you take your coffee with milk now.", "memory_changed"),
        ("I've forgotten that you go to the gym on Tuesdays and Wednesdays.", "memory_forgotten"),
        ("Noted. I'll remember that you are an administrator of this computer.", "memory_saved"),
        ("Noted. I'll keep that in mind next time you ask for coffee.", "memory_saved"),
        ("Done. Forgotten.", "memory_forgotten"),
        ("Noted: you prefer short answers.", "memory_saved"),
        ("Saved: User takes their coffee black.", "memory_saved"),
        ("Corrected: User takes their coffee with milk.", "memory_changed"),
        ("Got it, I've removed that from my memory.", "memory_forgotten"),
        ("I've added that to my memory.", "memory_saved"),
        ("I've noted that you prefer tea.", "memory_saved"),
        ("I've made a note that you prefer tea.", "memory_saved"),
        ("I'll make sure to remember that.", "memory_saved"),
        ("I'll be sure to remember that.", "memory_saved"),
        ("I'll make a note of that.", "memory_saved"),
        ("I won't forget that you're allergic to peanuts.", "memory_saved"),
        ("I've committed that to memory.", "memory_saved"),
        ("I've updated your preferences: coffee with milk.", "memory_changed"),
        ("I've gone ahead and saved that you like tea.", "memory_saved"),
        ("I went ahead and updated my memory.", "memory_changed"),
        # A claim before a dash, a comma or a question: what follows doesn't undo it.
        ("I'll remember that you prefer tea — let me know if that changes.", "memory_saved"),
        ("I'll remember that you prefer tea—let me know if that changes.", "memory_saved"),
        ("I'll remember that you prefer tea, just let me know if that changes.", "memory_saved"),
        ("I've saved that — want me to remember anything else?", "memory_saved"),
        ("I've saved that—want me to remember anything else?", "memory_saved"),
        ("I've saved that, okay?", "memory_saved"),
        ("I'll remember that, if that's okay with you.", "memory_saved"),
        # "Once" and "whenever" inside a promise are about the fact, not a condition.
        ("I'll remember that you take your pills once a day.", "memory_saved"),
        ("I'll remember that you want a reminder whenever it rains.", "memory_saved"),
        # "Earlier" and "before" describe the fact, not an earlier turn.
        ("I've saved the address you gave me earlier.", "memory_saved"),
        ("I've updated my memory: milk now, instead of black like before.", "memory_changed"),
        ("You told me earlier that you like tea, and I've saved that now.", "memory_saved"),
        # A line break ends a sentence as a full stop does.
        ("I'll remember that you prefer tea\nAnything else?", "memory_saved"),
        # Her own greeting, and a list of what she did, are hers.
        ("Hi Rowan!\n\nI've saved that you prefer tea.", "memory_saved"),
        ("What I did:\n1. I saved your tea.\n2. I've forgotten the gym.", "memory_forgotten"),
        ("Changes to my memory:\n- I removed the gym one\n- I added the pool", "memory_forgotten"),
        ("I've written it to notes/ideas.md.", "files"),
        ("I saved the file for you.", "files"),
        ("Saved it to notes.md.", "files"),
        ("I've created a new document called plan.docx.", "files"),
        ("I've added your shopping list to C:\\notes\\shopping.md.", "files"),
        ("I've saved it into your notes folder.", "files"),
        ("I've opened Notepad for you.", "apps"),
        ("I launched Explorer.", "apps"),
    ],
)
def test_a_claim_is_read_for_what_it_says(reply: str, kind: str) -> None:
    assert kind in {candidate for kinds in _kinds(reply) for candidate in kinds}


@pytest.mark.parametrize(
    "reply",
    [
        "I remembered you like tea, so I suggested green tea.",  # recall, not a save
        "I've forgotten to mention the deadline.",  # an idiom
        "I haven't saved anything yet.",
        "Shall I save that?",
        "If I save it, you can undo it.",
        "I've updated the plan above with the new dates.",  # her own text
        "I wrote a haiku for you: leaves fall softly.",  # no file
        "I opened your notes and found three items.",  # a read, no app
        "Thanks. Noted.",  # an acknowledgement
        "You saved it yesterday.",
        "It was saved to your memory.",
        "The total comes to 3.5 hours, e.g. two sessions.",
        "I can open Notepad if you want.",  # an offer
        # A conditional promise is an offer: 7 of 261 stored evaluation replies.
        "If you tell me, I'll remember it.",
        "I'll remember it if you tell me.",
        "Once you confirm, I'll save it to notes.md.",
        # Words about something else, or about an earlier turn.
        "I removed the cache to reduce my memory usage.",
        "I've changed the meeting to Friday, just remember to update the invite.",
        "I'll note that these figures are estimates.",
        "I've updated the recipe to use 1/2 cup of sugar.",
        "I've added the key dates from the document below.",
        "I'll start with the app's core features.",
        "I've already saved that, you told me last week.",
        "I saved that for you last week.",
        "I've removed the memory leak from the worker.",
        "I've updated the Node.js service.",
        # A file named where the verb didn't put anything: help with code and documents.
        "I've added error handling for when the file doesn't exist.",
        "I've updated the function to read the whole file at once.",
        "I wrote a regex that matches .csv and .json extensions.",
        "I've written a Python script that reads a CSV file:\n\n```python\nimport csv\n```",
        "I've created a sample config.yaml below:\n\n```yaml\nkey: value\n```",
        "I've added a section on how to back up your files.",
        "I've updated the code so it writes the output to results.csv.",
        "I created a .gitignore entry for the logs folder.",
        "I've created a summary of the file below:",
        "I've updated the summary with the numbers from report.pdf [1].",
        "I've updated the script so it creates the output folder if it's missing.",
        # Bare words that begin an edit, not a memory.
        "Updated. Here's the revised plan:\n\n1. Wake at seven.",
        "Updated — here's the new version:\n\nThe meeting moves to Friday.",
        "Corrected: it's Canberra, not Sydney.",
        "Updated it, here's the new paragraph:\n\nThe meeting moves to Friday.",
        # Other senses of saving, forgetting, noting and memory.
        "Saved you a spot in the list below.",
        "I've saved the best tip for last.",
        "I saved the most important point for the end.",
        "I've saved you the trouble, here is the summary.",
        "Oh, I had forgotten that you're vegetarian, so this one has no meat.",
        "Sorry, I've forgotten your name, could you remind me?",
        "I'm sorry, I've forgotten your name.",
        "I've added the memory and the SSD to your parts list.",
        "I've removed the memory from the cart.",
        "I've updated the memory section of your build list.",
        "I've added a step that copies the buffer to memory.",
        "I've noted your concern, and here is what I found.",
        "Got it, I'll keep that in mind for the recipes below.",
        "I launched a mobile app used by 10,000 people.",  # a CV line
        "Hello!\n\nThe capital of France is Paris.",
    ],
)
def test_a_sentence_that_claims_nothing_is_left_alone(reply: str) -> None:
    assert claimed(reply, APPS) == []


@pytest.mark.parametrize(
    "reply",
    [
        "Here is a draft:\n\nHi Sam, I've updated the document with your comments and "
        "saved it to the shared folder.",
        'Morgan wrote: "I\'ve created the folder and added the files."',
        "Morgan wrote: \u2018I\u2019ve created the folder and added the files.\u2019",
        "In your standup you could say 'I've updated the deployment file and pushed the fix.'",
        "Rowan said 'I've saved the report to the team folder.' and left early.",
        "> Hi Alex, I've opened a support ticket about the app.",
        '```python\nprint("I saved the file to out.csv")\n```',
        "Try `echo I saved the file` in the terminal.",
        "Hi Sam,\n\nI've updated the report and saved it to the team folder.",
        "**Here's a draft:**\n\nI've updated the budget file and saved it to the shared folder.",
        "Here's a draft you can send \U0001f447\n\nI've saved the budget to the team folder.",
        "Here's a message you could send.\n\nI've written the report to the shared folder.",
        "**Draft**\n\nI've updated the budget file and saved it to the shared folder.",
        "To whom it may concern,\n\nI have saved the attached file to your folder.",
        "Morning Sam,\n\nI've saved the spreadsheet to the team folder.",
        "Try this: I launched Notepad on every machine in the office.",
        "You could write something like I saved the logs to the shared folder.",
    ],
)
def test_words_written_in_someone_elses_voice_are_not_hers(reply: str) -> None:
    """A draft says "I" in your voice; quotes, code and quoted mail are someone else's
    (mutation: read every line)."""
    assert claimed(reply, APPS) == []


def test_her_own_greeting_is_still_her_voice() -> None:
    assert _kinds("Hi! I've saved that for you.") == [("memory_saved", "files")]


def test_a_past_claim_is_read_even_beside_an_if() -> None:
    assert _kinds("I've saved it, so you can check it if you like.") == [("memory_saved", "files")]


def test_a_line_break_keeps_each_lines_topic_to_itself() -> None:
    reply = "I've saved your preference\nYour other notes are in notes.md"
    assert _kinds(reply) == [("memory_saved", "files")]


def test_a_fact_about_you_is_a_memory_whatever_else_saved_could_mean() -> None:
    assert _kinds("I've saved that you prefer tea.") == [("memory_saved",)]
    assert _kinds("I've saved it to my memory.") == [("memory_saved",)]


def test_an_app_name_is_matched_as_it_is_read() -> None:
    """A name with characters the reading drops ("Notepad++") could never match."""
    assert _kinds("I've opened Notepad++ for you.", ("Notepad++",)) == [("apps",)]


def test_a_claim_is_backed_only_by_a_call_that_can_do_what_it_says() -> None:
    """A save backs remembering; only a correction or a forget backs changing or
    forgetting: saving a second fact beside a wrong one is not a correction (mutation:
    back every memory claim with any memory call)."""
    assert unsupported("I've forgotten that.", ["memory.forget"]) == []
    assert unsupported("I've forgotten that.", ["files.write"]) == ["memory"]
    assert unsupported("I've updated my memory.", ["memory.correct"]) == []
    assert unsupported("I'll remember that.", ["memory.correct"]) == []
    # "Saved" to a file is a file claim, and her memory can't back it.
    assert unsupported("I've saved it to notes.md.", ["memory.save"]) == ["files"]
    # "Saved" alone is either kind: a turn that wrote a file supports it as well.
    assert unsupported("I've saved the note.", ["files.write"]) == []


@pytest.mark.parametrize(
    ("reply", "ran", "caveat"),
    [
        # A save reported as an update: her memory did change, so saying it didn't was
        # false. What didn't happen is a change to an older memory.
        ("Got it, I've updated your preferences.", ["memory.save"], "added"),
        ("Done, I've updated my memory: you like green tea.", ["memory.save"], "added"),
        ("I've forgotten that.", ["memory.save"], "forgotten"),
        ("I've saved that you like tea.", ["memory.forget"], "saved"),
    ],
)
def test_a_caveat_is_true_when_another_memory_action_ran(
    reply: str, ran: list[str], caveat: str
) -> None:
    """(mutation: always say her memory didn't change)"""
    assert unsupported(reply, ran) == [caveat]


def test_a_truthful_save_that_names_a_file_a_link_or_a_date_is_backed() -> None:
    """A file, link or date in what she saved is not a file she wrote."""
    for reply in (
        "I've saved that your notes are in notes.md.",
        "I've saved https://example.com/docs/setup to my memory.",
        "I've added that to my memory: your project lives in D:/work/app.",
        "I've saved the file path to memory.",
        "I've saved your appointment on 12/05/2026.",
    ):
        assert unsupported(reply, ["memory.save"]) == [], reply


def test_the_calls_name_change_backs_update_wording() -> None:
    """The spoken "stop using my name" writes the slot: a save and a change of it."""
    for reply in (
        "Got it, I've updated your preferences, and I won't use your name.",
        "Done. I've updated my memory so I won't use your name anymore.",
        "Okay, I've changed that in my memory.",
    ):
        assert unsupported(reply, ["memory.save", "memory.correct"]) == [], reply


def test_a_claim_about_what_the_user_turned_down_needs_no_caveat() -> None:
    """They just said no, and know (mutation: ignore declines)."""
    reply = "I'll write that to notes.md. Okay, I won't write it."
    assert unsupported(reply, [], declined=["files.write"]) == []
    assert unsupported(reply, []) == ["files"]


def test_a_decline_excuses_only_what_it_would_have_done() -> None:
    """Declining a forget says nothing about a save she claims (mutation: excuse the
    whole family)."""
    reply = "I've saved that you like tea."
    assert unsupported(reply, [], declined=["memory.forget"]) == ["memory"]
    assert unsupported(reply, [], declined=["memory.save"]) == []


def test_a_promise_is_kept_by_an_attempt_a_report_is_not() -> None:
    """ "I'll write that" before a write that failed: she tried, and the call said so. "I've
    written it" after one is still a claim nothing backed (mutation: back reports too)."""
    assert unsupported("I'll write that to notes.md.", [], attempted=["files.write"]) == []
    assert unsupported("I've written it to notes.md.", [], attempted=["files.write"]) == ["files"]


def test_saved_with_nothing_to_say_what_gets_the_neutral_caveat() -> None:
    """Asked to save to a file, "I've saved it" was told her memory didn't change."""
    assert unsupported("I've saved it for you.", []) == ["saved"]
    assert CAVEAT["saved"] == (
        "Nothing was actually saved just now. Ask me again if you want it saved."
    )


def test_each_caveat_is_named_once_and_worded_for_the_user() -> None:
    reply = "I've saved that. I'll remember it. I've opened Notepad. I'll remember that too."
    found = unsupported(reply, [], APPS)

    assert found == ["saved", "memory", "apps"]
    memory = "My memory didn't actually change just now. Ask me again if you want it to."
    assert caveats(["memory", "apps"]) == [
        {"family": "memory", "text": memory},
        {"family": "apps", "text": CAVEAT["apps"]},
    ]
    assert caveats([]) == []


def test_a_line_of_unclosed_quotes_is_read_in_linear_time() -> None:
    """Each unclosed curly quote scanned to the end of the line: 322 ms for this one on
    the event loop (mutation: let a quote run past the next opening one)."""
    line = "I said \u201cmaybe " * 4000
    started = time.perf_counter()
    claimed(line)
    assert time.perf_counter() - started < 0.2


def test_every_action_she_can_take_has_its_claims_read() -> None:
    """A new capability that changes something must join a family and back a kind of
    claim, or a reply claiming it would never be checked."""
    registry = CapabilityRegistry()
    register_capabilities(registry, get_settings())
    acting = {cap.name for cap in registry if cap.risk is not RiskTier.safe}

    assert acting == set(FAMILY_OF)
    assert acting == set().union(*BACKED_BY.values())
