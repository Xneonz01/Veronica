"""Does an instruction planted in what she reads get carried out? The text-chat prompt
and tools exactly as a turn sends them, with the instruction in one of the three places
a turn reads text the user didn't write: a document it cites, her memory block, or a
file she was asked to read, as the result of that read. And a call's prompt and tools
(`spoken`), on what a call reads: her memory and a file's contents, never a document.
The model's first round is recorded, never run, and judged by `scoring.py`, which puts
each call that carries the instruction out to the kernel's own rules: what counts is
what would have run without anyone being asked.

A case plants either a request for tools, with words that show a call is carrying it
out, or a claim for her to pass on. Controls hold imperatives meant for the human
reader ("Remember to descale the kettle"), which no tool should answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.core.config import Settings
from app.core.event_bus import InProcessEventBus
from app.kernel.capability import CapabilityResult
from app.kernel.confirm import ConfirmationBroker
from app.kernel.kernel import CapabilityKernel
from app.kernel.policy import PolicyStore
from app.kernel.registry import CapabilityRegistry
from app.kernel.taint import Source, Taint
from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt
from app.services.tools.render import render_result
from eval.common import read_jsonl
from eval.injection.scoring import Outcome, score
from eval.material import documents_block, memory_block
from eval.tools.gate import confined_registry
from eval.turn import run_turn

CASES = Path(__file__).with_name("cases.jsonl")
VECTORS: tuple[Source, ...] = ("document", "memory", "tool_result")
# The read the user asked for, shaped as Ollama returns a call and a turn keeps it.
_READ_CALL_ID = "call_r3ad0001"


class _NoAudit:
    """Nothing runs, so nothing is recorded: the kernel is only asked what it would do."""

    async def record(self, **_entry: object) -> None:
        return None

    def record_detached(self, **_entry: object) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Surface:
    """What every case is put to: the prompt and tools a text turn sends, and the
    kernel that would decide on its calls, file tools confined to a throwaway folder."""

    system: str
    tools: list[dict[str, Any]]
    registry: CapabilityRegistry
    policy: PolicyStore
    kernel: CapabilityKernel
    root: str  # the folder, as the cases' {root} is written
    taint_min_words: int
    spoken: bool  # a call's: it reads a file's result cut as a call cuts it


@dataclass(frozen=True, slots=True)
class Reading:
    """One case as the model reads it, and what the kernel checks its calls against:
    what the turn read, by source, and what the user said; and what it read as text, for
    the check on what her reply passes on (`tools/relayed.py`)."""

    messages: list[dict[str, Any]]
    taint: Taint
    read: tuple[str, ...] = ()


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    for case in cases:
        _check(case)
    return cases


def _check(case: dict[str, Any]) -> None:
    """Refuse a case that can't be scored as written: one with nothing to recognise its
    attack by would count any call of the tool, or any reply, as carrying it out."""
    name = case["id"]
    if case["vector"] not in VECTORS:
        raise ValueError(f"{name}: unknown vector {case['vector']!r}")
    if case["kind"] == "attack":
        attack = case.get("attack") or {}
        if ("claim" in attack) == ("tools" in attack):
            raise ValueError(f"{name}: an attack plants a claim or asks for tools")
        if "tools" in attack and not attack.get("words"):
            raise ValueError(f"{name}: a tool attack names the words that carry it out")
        if "claim" in attack and not attack["claim"].get("must"):
            raise ValueError(f"{name}: a claim names the words that make it")
    elif case["kind"] != "control" or "acceptable" not in case:
        raise ValueError(f"{name}: a case is an attack or a control")
    if case["vector"] == "document" and not case.get("title"):
        raise ValueError(f"{name}: a document has a title")
    if case["vector"] == "tool_result" and not case.get("path"):
        raise ValueError(f"{name}: a file result names the file")


def surface(settings: Settings, root: Path, *, spoken: bool = False) -> Surface:
    registry = confined_registry(settings, root)
    bus = InProcessEventBus()
    policy = PolicyStore(bus, settings.capabilities.policy)
    floor = settings.capabilities.taint_min_words
    kernel = CapabilityKernel(
        registry, policy, ConfirmationBroker(bus), _NoAudit(), bus, taint_min_words=floor
    )
    system = build_prompt(DEFAULT_PERSONALITY, spoken=spoken, tools_enabled=True)
    tools = registry.schemas(registry.offered(call=spoken))
    return Surface(system, tools, registry, policy, kernel, root.as_posix(), floor, spoken)


def reading(case: dict[str, Any], on: Surface) -> Reading:
    """The case's messages, built as a text turn builds them (`ChatSession`,
    `chat/context.py`, `chat/replies.py`) or, on a call's surface, as a call does (the
    same shape, `realtime/workers.build_messages`), and what the turn read."""
    utterance = case["utterance"].replace("{root}", on.root)
    material = case["material"].replace("{root}", on.root)
    recalled = [*case.get("recalled", []), *([material] if case["vector"] == "memory" else [])]
    read: list[tuple[Source, str]] = []
    if block := memory_block(recalled):
        read.append(("memory", block))
    if case["vector"] == "document":
        read.append(("document", documents_block([(case["title"], material)])))
    system = "\n\n".join([on.system, *(text for _source, text in read)])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": utterance},
    ]
    if case["vector"] == "tool_result":
        # The read the user asked for, and its result, as the round before this one.
        read_back = CapabilityResult(ok=True, data={"content": material})
        result = render_result("files.read", read_back, for_voice=on.spoken)
        path = f"{on.root}/{case['path']}"
        request = {
            "id": _READ_CALL_ID,
            "function": {"index": 0, "name": "files_read", "arguments": {"path": path}},
        }
        messages += [
            {"role": "assistant", "content": "", "tool_calls": [request]},
            {"role": "tool", "content": result},
        ]
        read.append(("tool_result", result))
    taint = Taint.of(untrusted=read, trusted=[utterance])
    return Reading(messages, taint, tuple(text for _source, text in read))


async def run_case(llm: OllamaLLMClient, model: str, case: dict[str, Any], on: Surface) -> Outcome:
    built = reading(case, on)
    turn = await run_turn(llm, built.messages, model=model, tools=on.tools)
    return score(case, turn, built.taint, on, read=built.read)
