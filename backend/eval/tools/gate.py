"""Can a model drive Veronica's tools? The text-chat prompt and tool list exactly as a
turn sends them, one user message per case, and the model's first calls recorded,
never run; and the same on a call's prompt and tools, as its own lane (`call_...`),
since the spoken prompt moves the model differently. The file tools are confined to a
throwaway folder tree.

A case can carry `recalled`: memories placed in the prompt as the turn's memory block,
as retrieval would. Asked to forget something with its memory in view, the model
sometimes answered that it had, and called nothing; a case without the block can't
show that. And `history`: the turns before its message, a reply with `caveats` carrying
them after it as chat keeps them (`claims.with_caveats`). After a reply that said she
did what no tool did, "yes, please do" is asked of a history that says it was done.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, cast

from app.adapters.llm.ollama_client import OllamaLLMClient
from app.capabilities import register_capabilities
from app.capabilities.paths import PathRefused, resolve_roots, resolve_within
from app.core.config import Settings
from app.kernel.registry import CapabilityRegistry
from app.services.persona.defaults import (
    DEFAULT_PERSONALITY,
    EXPRESSIVE_BY_DEFAULT,
    build_prompt,
    with_expression,
)
from app.services.tools.claims import with_caveats
from eval.common import percentile, ratio, read_jsonl, says
from eval.material import memory_block
from eval.turn import call_arguments, run_turn

if TYPE_CHECKING:
    from app.services.connected.runtime import ConnectedRuntime

CASES = Path(__file__).with_name("cases.jsonl")

RULES = """How each case is scored:
- a tool case is right when the model's first call is the expected tool; leading
  with another tool is a wrong tool, and calling nothing is silent;
- arguments are checked only when the tool was right, so a wrong tool and a bad path
  are two failures, not one;
- a no-tool case is a false call when anything not listed as acceptable was called."""

# Folders the cases name, so a path the model copies from the question resolves.
_TREE = {
    "notes/shopping.md": "Shopping list\n- oat milk\n- lemons\n",
    "notes/todo.md": "- Book the roofer\n- Renew the car insurance\n",
    "projects/garden-plan.txt": "Raised beds along the south fence; carrots and beans.\n",
    "letters/landlord.txt": "Dear landlord, the boiler is making a noise again.\n",
}


@dataclass(slots=True)
class CaseOutcome:
    id: str
    kind: str
    expected: str | None
    called: list[str]
    first_ms: float | None
    # Only when the tool was right; None otherwise, so it isn't scored.
    args_ok: bool | None = None
    args_seen: dict[str, Any] = field(default_factory=dict)

    @property
    def right(self) -> bool:
        return self.expected is not None and self.called[:1] == [self.expected]


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    for case in cases:
        if case["kind"] not in ("tool", "no_tool"):
            raise ValueError(f"{case['id']}: unknown kind {case['kind']!r}")
        if case["kind"] == "tool" and not case.get("expect"):
            raise ValueError(f"{case['id']}: a tool case names the tool it expects")
    return cases


def build_tree(root: Path) -> None:
    for relative, text in _TREE.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


class _WebOn:
    """The web switched on, as far as offering its tools goes: an evaluation never runs
    them."""

    class store:  # noqa: N801 - stands in for `ConnectedRuntime.store`
        @staticmethod
        def web_on() -> bool:
            return True


#: The web for `register_capabilities`, switched on.
WEB_ON = cast("ConnectedRuntime", _WebOn())


def confined_registry(settings: Settings, root: Path, *, web: bool = False) -> CapabilityRegistry:
    """The capabilities a turn offers, file tools confined to `root`; with `web`, as
    a turn offers them while web search is switched on."""
    confined = settings.model_copy(
        update={
            "capabilities": settings.capabilities.model_copy(update={"file_roots": [str(root)]})
        }
    )
    registry = CapabilityRegistry()
    register_capabilities(registry, confined, web=WEB_ON if web else None)
    return registry


def tool_surface(
    settings: Settings, root: Path, *, spoken: bool = False, expressive: bool | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """The system prompt and tool list a text turn sends, file tools confined to `root`,
    or with `spoken` a call's: its spoken prompt, with her sounds as a call starts out
    (`expressive` says otherwise), and only the tools a call offers."""
    system = build_prompt(DEFAULT_PERSONALITY, spoken=spoken, tools_enabled=True)
    if spoken:
        system = with_expression(
            system, EXPRESSIVE_BY_DEFAULT if expressive is None else expressive
        )
    registry = confined_registry(settings, root)
    return system, registry.schemas(registry.offered(call=spoken))


def with_memory(system: str, recalled: list[str]) -> str:
    """The system prompt with these memories as the turn's memory block, as a text turn
    puts it (`chat/window.py`'s `turn_messages`)."""
    block = memory_block(recalled)
    return f"{system}\n\n{block}" if block else system


def check_arguments(rules: dict[str, Any], args: dict[str, Any], roots: list[Path]) -> bool:
    """Whether the arguments would do what the case asked, judged as the product would."""
    for name, rule in rules.items():
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            return False
        if "is" in rule or "within" in rule:
            try:
                real = resolve_within(value, roots)
            except PathRefused:
                return False
            if "is" in rule and real != resolve_within(str(roots[0] / rule["is"]), roots):
                return False
        if "must" in rule and not says(value, rule["must"]):
            return False
        if "one_of" in rule and value.strip().casefold() not in rule["one_of"]:
            return False
    return True


def _hide_root(value: Any, root_text: str) -> Any:
    # Results never carry a machine path: the tree's location is shown as {root}.
    if isinstance(value, str):
        return value.replace(root_text, "{root}").replace(root_text.replace("/", "\\"), "{root}")
    return value


async def run_case(
    llm: OllamaLLMClient,
    model: str,
    case: dict[str, Any],
    surface: tuple[str, list[dict[str, Any]]],
    root: Path,
) -> CaseOutcome:
    system, tools = surface
    root_text = root.as_posix()
    utterance = case["utterance"].replace("{root}", root_text)
    system = with_memory(system, case.get("recalled", []))
    messages = [
        {"role": "system", "content": system},
        *_history(case.get("history", []), root_text),
        {"role": "user", "content": utterance},
    ]
    turn = await run_turn(llm, messages, model=model, tools=tools)
    outcome = CaseOutcome(case["id"], case["kind"], case.get("expect"), turn.names, turn.first_ms)
    if outcome.right:
        args = call_arguments(turn.calls[0])
        outcome.args_ok = check_arguments(case.get("args", {}), args, resolve_roots([str(root)]))
        outcome.args_seen = {key: _hide_root(value, root_text) for key, value in args.items()}
    return outcome


def _history(turns: list[dict[str, Any]], root_text: str) -> list[dict[str, Any]]:
    """A case's turns before its message, as chat sends them: a reply's caveats after it."""
    return [
        {
            "role": turn["role"],
            "content": with_caveats(turn["content"], turn.get("caveats", [])).replace(
                "{root}", root_text
            ),
        }
        for turn in turns
    ]


def summarize(outcomes: list[CaseOutcome], cases: list[dict[str, Any]]) -> dict[str, Any]:
    tool = [o for o in outcomes if o.kind == "tool"]
    quiet = [o for o in outcomes if o.kind == "no_tool"]
    acceptable = {case["id"]: set(case.get("acceptable", [])) for case in cases}
    false_calls = [o for o in quiet if any(n not in acceptable[o.id] for n in o.called)]
    right = [o for o in tool if o.right]
    timings = [o.first_ms for o in outcomes if o.first_ms is not None]
    return {
        "selection": ratio(len(right), len(tool)),
        "wrong_tool": ratio(sum(1 for o in tool if o.called and not o.right), len(tool)),
        "silent": ratio(sum(1 for o in tool if not o.called), len(tool)),
        "false_call": ratio(len(false_calls), len(quiet)),
        "args_valid": ratio(sum(1 for o in right if o.args_ok), len(right)),
        "first_ms_median": None if not timings else round(median(timings), 1),
        "first_ms_p90": percentile(timings, 0.9),
        "cases": [asdict(o) for o in outcomes],
    }
