"""The ingest loop: microphone frames in, utterances out.

`vad_pump` hands each microphone frame to the voice-detection thread (`VadWorker`), and
`ingest_loop` takes it back with its turn events, in order, and drives the turns. The
loop never awaits recognition or generation (the STT task spawns the reply), so voice
detection, and with it barge-in, stays live for the whole call.

**The pre-roll.** The detector says "speech" only after `min_speech_ms` (250 ms) of it,
and a word's onset is quieter than the vowel that convinces it. So frames no utterance
has taken wait in a ring of the last 400 ms, and an utterance opens with them: without
them the call probe's "What's the capital of France?" reached recognition as "the
capital of France?". The ring is capped, since seconds of room noise ahead of every
utterance would give recognition a worse problem, and it is measured in audio rather
than frames, so the cap doesn't depend on how a client sizes its frames.

An interruption while her voice is playing opens with its own frame only: the ring was
recorded over her voice, and recognition would take whatever the echo cancellation left
of it for the user's words. Playing, not "speaking": the state follows synthesis, and
the client plays on after it (`BargeInGate.playing`). Before any of her audio has gone
out (the final decode, the model's first tokens) the ring holds only the user, so
someone picking up again after a pause keeps their first word.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from app.services.voice.turn import TurnEvent

if TYPE_CHECKING:
    from app.services.voice.realtime.session import RealtimeSession

PREROLL_BYTES = 12_800  # 400 ms of 16 kHz PCM16


def keep(ring: deque[bytes], frame: bytes) -> None:
    """Add a frame, dropping the oldest while the rest still cover the pre-roll."""
    ring.append(frame)
    while len(ring) > 1 and sum(map(len, ring)) - len(ring[0]) >= PREROLL_BYTES:
        ring.popleft()


def drain(ring: deque[bytes]) -> list[bytes]:
    """Everything in the ring, oldest first, leaving it empty for the next utterance."""
    frames = list(ring)
    ring.clear()
    return frames


async def vad_pump(s: RealtimeSession) -> None:
    """Hand microphone frames to the voice-detection thread, in the order they came."""
    worker = s.vad_worker
    if worker is None:
        return
    while True:
        frame = await s.audio_in.get()
        if frame is None:
            return
        worker.submit(frame)


async def ingest_loop(s: RealtimeSession) -> None:
    """Take scored frames back; route them to the active utterance; drive turns."""
    worker = s.vad_worker
    if worker is None:
        raise RuntimeError("the voice-detection thread isn't running")
    ring: deque[bytes] = deque()
    while True:
        result = await worker.results.get()
        if result is None:  # the thread has stopped: the call is over
            return
        frame, events = result.frame, result.events
        taken = s.utterance_q is not None
        if s.utterance_q is not None:
            s.utterance_q.put_nowait(frame)
        over_her = s.barge.playing  # read first: an interruption disarms the gate
        if s.barge.should_interrupt(events):
            opening = [frame] if over_her else [*drain(ring), frame]
            ring.clear()
            await s.trigger_barge_in(*opening)
            continue
        for event in events:
            if event is TurnEvent.speech_start and s.utterance_q is None:
                # While a spoken confirmation is pending, speech must get through even
                # though a turn is in flight: it is the answer. Otherwise the answer is
                # dropped and the confirmation always times out. `spoken_confirm`
                # explains why the barge-in gate is off here too.
                if s.awaiting_confirm is not None:
                    s.begin_utterance(*drain(ring), frame)
                    continue
                # `turn_in_flight` covers the window between speech_end and the reply:
                # the decode is still running, so without it a mid-sentence pause would
                # start a second utterance and run two replies over one chunker.
                if s.turn_in_flight:
                    continue
                s.begin_utterance(*drain(ring), frame)
            elif event is TurnEvent.speech_end:
                s.end_utterance(last_voiced=result.scored_at - result.trailing_silence_ms / 1000)
        # Only a frame no utterance has: the one that opened an utterance is already its
        # last, and the one that closed an utterance went to it.
        if not taken and s.utterance_q is None:
            keep(ring, frame)
