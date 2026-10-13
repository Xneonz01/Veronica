"""Barge-in: stop talking, keep only what was heard, and give the user the floor.

A free function over `RealtimeSession`, like `workers.py` and `lifecycle.py`, so the
session stays the state machine and this owns the one operation whose ordering matters
in every direction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.tools import claims
from app.services.voice.realtime.session_emit import State
from app.services.voice.realtime.voice_events import barge_in_event, playback_flush_event

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession


async def barge_in(s: RealtimeSession, *frames: bytes) -> None:
    """Interrupt her: the speech that did it, `frames`, opens the next utterance."""
    # Invalidate, cancel and drain with no await in between. Everything below awaits (a
    # socket write, a JSONL append) while the out-loop keeps sending, so a drain placed
    # after those would ship the cancelled reply to the client before the flush reached
    # it. The greeting too: it runs as its own task, and left alone it finished its
    # sentence, and held the voice model, while the user was already talking.
    s.audio_gen += 1
    for task in (s._respond_task, s._greeting_task):
        if task is not None and not task.done():
            task.cancel()
            task.add_done_callback(s._reap)
    s._respond_task = None
    s._greeting_task = None
    s._drain_audio_out()
    _keep_what_was_heard(s)
    # The interrupted turn is measured (before the chunker resets), and the handle is
    # dropped so `respond` can't record it again.
    metrics = s.current_metrics
    s.current_metrics = None
    if metrics is not None:
        metrics.barged_in = True
        metrics.mark_done(len(s.chunker.spoken_text))
    s.chunker.reset()
    s.ledger.reset()
    s.played_samples = None
    s.turn_in_flight = False
    s.barge.disarm()
    # The new utterance takes over before anything awaits. The decode of what came before
    # can finish during the awaits below, and it has to find it is no longer the newest:
    # it used to be answered alone, while the user was still talking.
    s.begin_utterance(*frames)
    await s._set_state(State.listening)
    s._drain_audio_out()  # sweep anything the cancelled task queued mid-await
    await s._emit(barge_in_event(source=s._source))
    await s._emit(playback_flush_event(source=s._source))
    # After the flush, which is what silences her: it mustn't wait behind a disk write.
    if metrics is not None:
        await s.emit_metrics(metrics)


def _keep_what_was_heard(s: RealtimeSession) -> None:
    """Put what she was heard to say in history, marked as cut off.

    A reply interrupted while it played has usually been recorded already, when its
    last sentence was made; that entry is cut back to what was heard, wherever it sits,
    rather than a second copy added. Without a position from the client, nothing is
    known to have been heard: the client reports within 100 ms of a reply's first
    audio, and keeping what was made put words in history that never played. A caveat
    shown after a reply heard whole stays with it, however much of it was said
    (`claim_caveat.py`); one after a reply cut short goes with the rest.
    """
    entry, s.reply_entry = s.reply_entry, None
    caveats, s.reply_caveats = s.reply_caveats, []
    played = s.played_samples
    # From zero without a report: only words carried over from before a spoken
    # confirmation, which were heard then, count.
    heard = s.ledger.text_up_to(played if played is not None else 0).strip()
    cut = played is None or played < s.ledger.words_end
    content = f"{heard}…" if heard and cut and not heard.endswith("…") else heard
    if content and not cut:
        content = claims.with_stored(content, caveats)
    index = next((i for i, message in enumerate(s.history) if message is entry), None)
    if index is not None:
        if content:
            s.history[index]["content"] = content
        else:
            del s.history[index]  # cut off before a word of it was heard
    elif content:
        kept = {"role": "assistant", "content": content}
        s.history.append(kept)
        for source in sorted(s.turn_sources):  # it may repeat what a tool returned
            s.carried.append((source, kept))
