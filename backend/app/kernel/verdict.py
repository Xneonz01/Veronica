"""The decision on one request: run it, ask first, or refuse. Computed once, here.

`CapabilityKernel.execute` acts on it and `requires_confirmation` reports it, so a call
that speaks its question before the kernel waits (`voice/realtime/tool_loop.py`) can
never disagree with what the kernel then does. It only reads state: the budget is
counted by `execute`, right before a call runs (`kernel/budget.py`).

The gates, in order; the strictest outcome wins (refuse, then ask, then run):

1. **Available.** Switched off since the tool list was built: refused.
2. **Policy.** `deny` refuses; `confirm` asks.
3. **Budget.** A turn's or a minute's ceiling reached: refused.
4. **Provenance.** An argument copied word for word from what she read, and not from
   the user, asks (`kernel/taint.py`). Not for `safe` capabilities: a read changes
   nothing.
5. **After the web.** A turn whose prompt holds text from the web, read this turn or
   carried in an earlier reply, makes everything that isn't `safe` ask: a page's
   instruction reworded by the model gets past the copy check (ADR 0003). A tool
   server's result counts the same: its author is no more known than a page's.
6. **Egress.** A call that reaches the network asks when it would send an unfamiliar
   address, a secret, or the user's own words (`kernel/egress.py`). Gate 5 doesn't
   apply to those calls: a search after a search runs, as long as it sends nothing it
   shouldn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from app.kernel import egress
from app.kernel.budget import Budget
from app.kernel.capability import CapabilityRequest, RiskTier
from app.kernel.errors import Code
from app.kernel.policy import PolicyMode
from app.kernel.registry import Capability
from app.kernel.taint import Tainted, find

# Why a prompt was raised for a call its policy would have let run, so surfaces can say
# so. An argument came word for word from what she read:
FROM_READING = "from_reading"
# Or its turn read the web, copied or not:
AFTER_WEB = "after_web"
# Or what a tool server returned (`services/mcp`):
AFTER_MCP = "after_mcp"


class Action(StrEnum):
    run = "run"
    ask = "ask"
    refuse = "refuse"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What to do with a request, and why: the reason a prompt shows, or the code and
    words of a refusal. `tainted` is the copied argument, when gate 4 found one."""

    action: Action
    why: str | None = None
    code: Code | None = None
    detail: str = ""
    tainted: Tainted | None = None


_OFF = {
    "network": (Code.network_disabled, "Web search is switched off in Settings."),
    "local": (Code.unavailable, "That isn't available right now."),
    "external": (Code.unavailable, "That tool's server isn't running right now."),
}
_SPENT = "I've done that as many times as I can for now, so I stopped there."


def decide(
    capability: Capability,
    args: BaseModel,
    req: CapabilityRequest,
    mode: PolicyMode,
    *,
    budget: Budget | None,
    taint_min_words: int,
) -> Verdict:
    """The verdict on `req`, whose arguments validated as `args`."""
    if capability.available is not None and not capability.available():
        code, detail = _OFF[capability.reach]
        return Verdict(Action.refuse, code=code, detail=detail)
    if mode is PolicyMode.deny:
        return Verdict(
            Action.refuse, code=Code.policy_denied, detail="That action is switched off."
        )
    taint = req.taint
    turn = taint.turn if taint is not None else None
    if budget is not None and not budget.allows(capability.name, turn):
        return Verdict(Action.refuse, code=Code.budget_exceeded, detail=_SPENT)

    tainted = _copied(capability, req, taint_min_words)
    if mode is PolicyMode.confirm:
        # Asks whatever else is found; a reason is still worth showing.
        why = _egress_why(capability, args, req) if tainted is None else None
        return Verdict(Action.ask, why=why, tainted=tainted)
    if tainted is not None:
        return Verdict(Action.ask, why=FROM_READING, tainted=tainted)
    if taint is not None and capability.risk is not RiskTier.safe:
        if taint.read("web"):
            return Verdict(Action.ask, why=AFTER_WEB)
        if taint.read("mcp"):
            return Verdict(Action.ask, why=AFTER_MCP)
    why = _egress_why(capability, args, req)
    if why is not None:
        return Verdict(Action.ask, why=why)
    return Verdict(Action.run)


def _copied(capability: Capability, req: CapabilityRequest, min_words: int) -> Tainted | None:
    """The argument that came word for word from what she read, if one did (gate 4)."""
    if req.taint is None or capability.risk is RiskTier.safe:
        return None
    return find(
        req.args,
        capability.args_model,
        req.taint,
        capability=capability.name,
        min_words=min_words,
        selectors=capability.selectors,
    )


def _egress_why(capability: Capability, args: BaseModel, req: CapabilityRequest) -> str | None:
    found = egress.check(capability, args, req.taint)
    return found.why if found is not None else None
