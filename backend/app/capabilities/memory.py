"""Memory as capabilities: save, recall, correct and forget, run by the kernel.

Until 0.17 the model's memory tools had a path of their own beside the kernel, with no
policy, no confirmation and no audit row: a document that talked the model into saving
a sentence planted it in every later prompt, and nothing recorded that it happened. Run
by the kernel, they are checked, recorded and confirmable like every other action.

Saving and correcting run without asking by default (`auto_approve`): a prompt on every
fact she is told would be unbearable, and a new or corrected memory raises a
notification with Undo. Forgetting asks first, because a forgotten memory's
notification has no Undo. The user can change any of them in the capability policy.

When one of them does ask, its preflight speaks first: a request that could only end
in "couldn't" (nothing matches, a secret, something she already remembers) is refused
without a prompt, and the memories the prompt names are pinned on the arguments, so a
yes changes exactly those and not what a second look would find later.

A recall's result is her memories, so it reaches the model as quoted data (`content`)
and the screen as `shown`, never `detail`: the audit log keeps `detail` and can't be
edited, so a memory forgotten later would live on there.

Without a memory runtime (the evaluation harness, which only reads the tool list) the
four are still declared, so the list is the one a turn sends, and they refuse if run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.capabilities.memory_args import CorrectArgs, ForgetArgs, RecallArgs, SaveArgs
from app.capabilities.memory_lookup import LOOSE, inventory, meant, quoted, quoted_text, targets
from app.capabilities.memory_profile import described, slot_value, unreadable
from app.kernel.capability import CapabilityResult, RiskTier
from app.kernel.errors import Code
from app.kernel.registry import Capability, CapabilityRegistry
from app.services.memory.address import classify_directive
from app.services.memory.assembler import RECALL_PREAMBLE, render_block
from app.services.memory.models import AddressByName, Memory, MemoryCreate, MemoryKind
from app.services.memory.sensitive import SensitiveContentError, is_sensitive

if TYPE_CHECKING:
    from app.services.memory.runtime import MemoryRuntime

_ADDRESS_ACK = {
    AddressByName.never: "Understood — I won't use your name.",
    AddressByName.sparingly: "Understood — I'll use your name sparingly.",
    AddressByName.often: "Understood — I'll use your name more.",
}


def _refused(code: Code, detail: str) -> CapabilityResult:
    return CapabilityResult(ok=False, code=code.value, detail=detail)


def _unavailable() -> CapabilityResult:
    return _refused(Code.memory_unavailable, "My memory isn't available right now.")


def _secret() -> CapabilityResult:
    return _refused(Code.sensitive_content, "I won't store that — it looks like a secret.")


def _unfit(text: str, blank: str) -> CapabilityResult | None:
    """The refusal for words she won't keep, checked before anyone is asked about them:
    a prompt would show the secret on screen and say it aloud on a call."""
    if not text.strip():
        return _refused(Code.invalid_args, blank)
    return _secret() if is_sensitive(text) else None


async def correct_slot(memory: MemoryRuntime, slot: Memory, args: CorrectArgs) -> CapabilityResult:
    """A correction of her profile: the slot superseded like a fact, so its notification
    has Undo, with the value read from the new phrase, or refused asking for it (bug 38)."""
    value = slot_value(slot, args.old, args.new)
    if value is None:
        return unreadable(slot)
    try:
        await memory.service.supersede(
            slot.id,
            MemoryCreate(
                kind=MemoryKind.profile,
                key=slot.key,
                content=value,
                importance=slot.importance,
                pinned=slot.pinned,
                source="user",
                confidence=1.0,
            ),
        )
    except SensitiveContentError:
        return _secret()
    return CapabilityResult(ok=True, detail=f"Changed your {slot.key} to {value}.")


def register_memory(registry: CapabilityRegistry, memory: MemoryRuntime | None) -> None:
    """Declare the four memory capabilities, run against `memory`."""

    async def save(args: SaveArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        content = args.content.strip()
        if not content:
            return _refused(Code.invalid_args, "There was nothing to save.")
        directive = classify_directive(content)
        if directive is not None:
            # Form of address is a profile slot, not a stored preference, so the
            # correction is always on and settles with the name in one place.
            await memory.service.set_address_mode(directive)
            return CapabilityResult(ok=True, detail=_ADDRESS_ACK[directive])
        # Active at once and marked as the user's: they asked for it, so it is not a
        # learning candidate waiting for review.
        create = MemoryCreate(
            kind=MemoryKind(args.kind), content=content, source="user", confidence=1.0
        )
        try:
            stored = await memory.service.add_if_new(create)
        except SensitiveContentError:
            return _secret()
        if stored is None:
            return CapabilityResult(ok=True, detail=f"Already remembered: {content}")
        return CapabilityResult(ok=True, detail=f"Saved: {content}")

    async def recall(args: RecallArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        query = args.query.strip()
        if not query:
            return _refused(Code.invalid_args, "No query was given.")
        # The per-turn preamble says "do not recite it back", which suits a block she
        # is handed, not a recall the user asked to hear.
        retrieved = await memory.retriever.retrieve(query)
        found = (
            render_block(retrieved, preamble=RECALL_PREAMBLE)
            if retrieved.items
            else await inventory(memory, retrieved)
        )
        if found is None:
            return CapabilityResult(ok=True, detail="I don't have anything relevant remembered.")
        return CapabilityResult(
            ok=True,
            detail="Looked through what I remember.",
            data={"content": found, "shown": found},
        )

    async def correct(args: CorrectArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        new = args.new.strip()
        if not new:
            return _refused(Code.invalid_args, "There was nothing to change it to.")
        old = await meant(memory, args)
        if isinstance(old, CapabilityResult):
            return old
        if old.kind is MemoryKind.profile:
            return await correct_slot(memory, old, args)
        try:
            # Superseded, not edited: the old memory is archived and linked, so Undo
            # brings it back and what she believed before isn't lost. What made it
            # matter carries over: a pinned allergy stays pinned once corrected.
            await memory.service.supersede(
                old.id,
                MemoryCreate(
                    kind=old.kind,
                    content=new,
                    importance=old.importance,
                    pinned=old.pinned,
                    source="user",
                    confidence=1.0,
                ),
            )
        except SensitiveContentError:
            return _secret()
        return CapabilityResult(ok=True, detail=f"Corrected: {new}")

    async def forget(args: ForgetArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        found = await targets(memory, args)
        if isinstance(found, CapabilityResult):
            return found
        for item in found:
            await memory.service.delete(item.id)
        count = len(found)
        return CapabilityResult(ok=True, detail=f"Forgot {count} thing{'' if count == 1 else 's'}.")

    # Before anyone is asked: the refusal each would end in, or what it will change,
    # named as she has it. The model's words for a memory came out of an older turn
    # once, and the prompt named a memory already corrected.
    async def save_first(args: SaveArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        refused = _unfit(args.content, "There was nothing to save.")
        if refused is None and await memory.service.remembers(args.content):
            refused = _refused(Code.already_exists, "I already remember that.")
        return refused or CapabilityResult(ok=True)

    async def correct_first(args: CorrectArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        old = _unfit(args.new, "There was nothing to change it to.") or await meant(
            memory, args, overlap=LOOSE
        )
        if isinstance(old, CapabilityResult):
            return old
        if old.kind is MemoryKind.profile:
            value = slot_value(old, args.old, args.new)
            if value is None:
                return unreadable(old)
            args.naming([old])
            return CapabilityResult(
                ok=True, detail=f"change your {old.key} from “{old.content}” to “{value}”"
            )
        args.naming([old])
        return CapabilityResult(
            ok=True, detail=f"replace {quoted([old])} with {quoted_text(args.new)}"
        )

    async def forget_first(args: ForgetArgs) -> CapabilityResult:
        if memory is None:
            return _unavailable()
        found = await targets(memory, args)
        if isinstance(found, CapabilityResult):
            return found
        args.naming(found)
        return CapabilityResult(ok=True, detail=f"forget {described(found)}")

    registry.register(
        Capability(
            name="memory.save",
            description=(
                "Save a durable fact the user asked you to remember about them. Also use "
                "this when the user tells you how to address them — e.g. to stop using "
                "their name."
            ),
            args_model=SaveArgs,
            risk=RiskTier.guarded,
            handler=save,
            voice_exposed=True,
            auto_approve=True,
            summarize=lambda a: f"remember {quoted_text(a.content)}",
            preflight=save_first,
        )
    )
    registry.register(
        Capability(
            name="memory.recall",
            description="Look up what you remember about the user, relevant to a query.",
            args_model=RecallArgs,
            risk=RiskTier.safe,
            handler=recall,
            voice_exposed=True,
            summarize=lambda a: f"look up what I remember about {a.query}",
        )
    )
    registry.register(
        Capability(
            name="memory.correct",
            description="Replace something you remembered about the user that they say is wrong.",
            args_model=CorrectArgs,
            risk=RiskTier.guarded,
            handler=correct,
            voice_exposed=True,
            auto_approve=True,
            summarize=lambda a: f"replace {quoted_text(a.old)} with {quoted_text(a.new)}",
            preflight=correct_first,
            selectors=frozenset({"old"}),
        )
    )
    registry.register(
        Capability(
            name="memory.forget",
            description="Forget something you remembered about the user, when they ask you to.",
            args_model=ForgetArgs,
            risk=RiskTier.guarded,
            handler=forget,
            voice_exposed=True,
            summarize=lambda a: f"forget {quoted_text(a.query)}",
            preflight=forget_first,
            selectors=frozenset({"query"}),
        )
    )
