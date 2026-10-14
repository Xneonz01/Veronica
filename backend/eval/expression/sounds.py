"""Does a model use her voice's sounds as a person would? A call's prompt and tools,
one user message per case, and the reply's tags and stage directions counted.

Two lanes on the same cases: the call's prompt as it was before 0.36.0, without the
sounds (`off`), and with them (`on`). The first shows what a model writes untold, which
is why the transcript filter exists; the second is what "Expressive voice" sends.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.adapters.tts.expression import SPOKEN_TAGS, ExpressionFilter, classify, considerate
from app.core.config_voice import RealtimeSettings
from app.services.persona.defaults import with_expression
from app.services.voice.realtime.chunker import SentenceChunker
from eval.common import ratio, read_jsonl
from eval.turn import run_turn

CASES = Path(__file__).with_name("cases.jsonl")
KINDS = ("funny", "letdown", "serious", "plain")

RULES = """How each reply is scored (lanes: off = the call's prompt without the sounds,
on = with them):
- a reply's sounds are those her voice is sent: a tag she may use, or a direction that
  names one ("*laughs*" is a laugh); a direction or tag written other than as one of
  her tags ("*laughs*", "[smiles]", "[happy]") is also improvised;
- tagged, funny_tagged, letdown_tagged: replies (of each kind) with any sound;
- fitting: a funny case answered with [laugh] or [chuckle], a letdown with [sigh];
- mocking: a laugh or a chuckle at something that went wrong for the user, small or
  serious: the worst mistake, since it sounds like mockery; mocking_voiced counts the
  ones her voice would still make, the reply streamed word by word through the call's
  filter, chunker and `considerate`, as a call says it;
- misplaced: any sound wrong for the moment: mocking, a sigh at a joke, or any sound at
  grief, fear or illness;
- plain_tagged: any tag on a plain question;
- over: more than one tag in a reply; empty: no words, only a tool call."""

_PIECE = re.compile(r"\[[^\]\n]{1,38}\]|\*[^*\n]{1,38}\*|\([^)\n]{1,38}\)")
_FITS = {"funny": {"laugh", "chuckle"}, "letdown": {"sigh"}}
# Sounds that are wrong for the moment: laughing at someone's bad luck or grief reads as
# mockery, and any sound at all at grief, fear or illness is out of place.
_WRONG = {"funny": {"sigh"}, "letdown": {"laugh", "chuckle"}, "serious": set(SPOKEN_TAGS)}


@dataclass(slots=True)
class Reply:
    id: str
    kind: str
    text: str
    called: list[str]
    tags: list[str] = field(default_factory=list)
    improvised: list[str] = field(default_factory=list)
    voiced: list[str] = field(default_factory=list)  # what her voice would make of them


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    for case in cases:
        if case["kind"] not in KINDS:
            raise ValueError(f"{case['id']}: unknown kind {case['kind']!r}")
    return cases


def read_sounds(reply: Reply, call: RealtimeSettings | None = None) -> Reply:
    """Fill in the sounds `reply.text` asks for, the improvised pieces it holds, and the
    sounds her voice would make of it on a call chunked as `call` says."""
    for match in _PIECE.finditer(reply.text):
        piece = match.group()
        tag = classify(piece)
        exact = piece.startswith("[") and piece[1:-1] == tag and tag in SPOKEN_TAGS
        if tag is not None and not exact:
            reply.improvised.append(piece)
    spoken, said = _as_a_call_says_it(reply.text, call or RealtimeSettings())
    reply.tags = _sounds(spoken)
    reply.voiced = _sounds(said)
    return reply


def _as_a_call_says_it(text: str, call: RealtimeSettings) -> tuple[str, str]:
    """`text` streamed a word at a time as `workers.respond` takes a reply: what the
    filter sends on, and what is said once the chunker and `considerate` have had it."""
    voice = ExpressionFilter(expressive=True)
    chunker = SentenceChunker(
        call.chunk_max_chars,
        clause_chunking=call.clause_chunking,
        min_clause_chars=call.min_clause_chars,
    )
    sent: list[str] = []
    said: list[str] = []
    for word in [*re.findall(r"\S+\s*", text), None]:
        part = voice.feed(word) if word is not None else voice.end()
        sent.append(part.spoken)
        chunks = chunker.push(part.spoken) + (chunker.flush() if word is None else [])
        said += [considerate(chunk, chunker.spoken_text) for chunk in chunks]
    return "".join(sent), " ".join(said)


def _sounds(text: str) -> list[str]:
    return [tag for tag in re.findall(r"\[([a-z]+)\]", text) if tag in SPOKEN_TAGS]


async def run_case(
    llm: OllamaLLMClient,
    model: str,
    case: dict[str, Any],
    surface: tuple[str, list[dict[str, Any]]],
    *,
    expressive: bool,
    call: RealtimeSettings | None = None,
) -> Reply:
    system, tools = surface
    messages = [
        {"role": "system", "content": with_expression(system, expressive)},
        {"role": "user", "content": case["utterance"]},
    ]
    turn = await run_turn(llm, messages, model=model, tools=tools)
    return read_sounds(Reply(case["id"], case["kind"], turn.text.strip(), turn.names), call)


def summarize(replies: list[Reply]) -> dict[str, Any]:
    spoke = [r for r in replies if r.text]
    of = {kind: [r for r in spoke if r.kind == kind] for kind in KINDS}
    fitting = [r for r in [*of["funny"], *of["letdown"]] if set(r.tags) & _FITS[r.kind]]
    hurt = [*of["letdown"], *of["serious"]]
    mocking = [r for r in hurt if {"laugh", "chuckle"} & set(r.tags)]
    voiced = [r for r in hurt if {"laugh", "chuckle"} & set(r.voiced)]
    misplaced = [r for r in spoke if set(r.tags) & _WRONG.get(r.kind, set())]
    return {
        "tagged": ratio(sum(1 for r in spoke if r.tags), len(spoke)),
        "fitting": ratio(len(fitting), len(of["funny"]) + len(of["letdown"])),
        "funny_tagged": ratio(sum(1 for r in of["funny"] if r.tags), len(of["funny"])),
        "letdown_tagged": ratio(sum(1 for r in of["letdown"] if r.tags), len(of["letdown"])),
        "mocking": ratio(len(mocking), len(hurt)),
        "mocking_voiced": ratio(len(voiced), len(hurt)),
        "misplaced": ratio(len(misplaced), len(spoke) - len(of["plain"])),
        "plain_tagged": ratio(sum(1 for r in of["plain"] if r.tags), len(of["plain"])),
        "improvised": ratio(sum(1 for r in spoke if r.improvised), len(spoke)),
        "over": ratio(sum(1 for r in spoke if len(r.tags) > 1), len(spoke)),
        "empty": ratio(len(replies) - len(spoke), len(replies)),
        "replies": [asdict(r) for r in replies],
    }
