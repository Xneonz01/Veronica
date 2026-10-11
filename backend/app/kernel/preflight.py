"""A capability's check before anyone is asked, held to its handler's rules.

Bounded by the capability's timeout, and a failure is a refusal on the record, never an
exception out of the kernel: a database error in the check before a forget ended the
whole chat turn, with no audit row. A cancellation is recorded like one that lands
while the kernel waits for an answer, then let through.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from app.kernel.capability import CapabilityResult
from app.kernel.errors import Code
from app.kernel.records import Records
from app.kernel.registry import Capability, Preflight

logger = logging.getLogger(__name__)


def as_code(value: str) -> Code:
    """A preflight's refusal code, or `capability_failed` for one the kernel doesn't know."""
    try:
        return Code(value)
    except ValueError:
        return Code.capability_failed


async def check(
    capability: Capability,
    preflight: Preflight,
    args: BaseModel,
    records: Records,
    *,
    actor: str,
    started: float,
) -> CapabilityResult:
    """What `preflight` says about `args`, or the refusal it failed with."""
    try:
        return await asyncio.wait_for(preflight(args), capability.timeout_s)
    except TimeoutError:
        return CapabilityResult(
            ok=False,
            code=Code.capability_timeout.value,
            detail="That took too long, so I stopped it.",
        )
    except asyncio.CancelledError:
        records.cancelled(
            capability.name, args, actor, started, None, "Cancelled before it was asked."
        )
        raise
    except Exception:
        logger.exception("the check before asking about %s failed", capability.name)
        return CapabilityResult(
            ok=False, code=Code.capability_failed.value, detail="That didn't work."
        )
