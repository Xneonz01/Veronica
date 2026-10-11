"""When learning tries again after a failed attempt, the same for a chat conversation
(`chat/learning_queue.py`) and a finished call (`call_queue.py`): a minute, doubling, at
most an hour. And the one reason kept for a failure that isn't an error's type: an
answer that wasn't the JSON asked for, which the worker answers by reading less next
time (`chat/learning_worker.py`).
"""

from __future__ import annotations

from datetime import datetime

#: Why an attempt failed when the model answered, but not in the JSON asked for.
UNREADABLE = "unreadable"

_FIRST_WAIT_S = 60
_LONGEST_WAIT_S = 3600


def stamp(moment: datetime) -> str:
    """A time as the queues store it: one fixed width, so stored times compare as text
    in the order they happened."""
    return moment.isoformat(timespec="microseconds")


def wait_after(attempts: int) -> int:
    """The seconds before the next attempt, after `attempts` failures in a row before
    this one."""
    return int(min(_FIRST_WAIT_S * 2**attempts, _LONGEST_WAIT_S))
