"""What she was heard to say, found from how far the client has played her reply.

History used to take the chunker's text on a barge-in: everything sent to the voice,
though text becomes a chunk before it is synthesised and long before it is played, and
the barge-in throws away queued audio nobody heard. The model then read the user's
correction against words that were never said out loud.

So each chunk's audio is recorded as a range of output samples, and given the client's
playback position, `text_up_to` returns only the words that had reached the speaker.
Samples rather than milliseconds or bytes, because the voice is 24 kHz mono PCM16 all
the way to the client, and a sample means the same to the synthesiser, the queue and
the client's audio clock.

Pure: no clock, no I/O, no session.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Span:
    """One chunk's text and its audio, as a half-open range of output samples."""

    text: str
    start: int
    end: int

    @property
    def samples(self) -> int:
        return self.end - self.start


class SpokenLedger:
    """Maps a playback position in the current reply to the text heard by then."""

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._total = 0
        self._heard_before = ""  # this reply's words heard before its current audio

    @property
    def total_samples(self) -> int:
        """All the audio queued for this reply: the most that can be played."""
        return self._total

    @property
    def words_end(self) -> int:
        """Where the last span with words ends. Audio after it (a question, a caveat)
        isn't the reply, so hearing up to here is hearing the reply whole. With no words
        in this ledger (all of them heard before a spoken confirmation), that is its
        start: a caveat said after the round isn't her words."""
        return max((span.end for span in self._spans if span.text), default=0)

    def begin(self, text: str) -> None:
        """Open a span for a chunk about to be synthesised.

        Its audio can come back as several buffers (the TTS adapter splits sentences),
        all of which belong to this text. Text that isn't part of the reply, such as a
        spoken question, opens a span with no text, so the samples still line up.
        """
        self._spans.append(Span(text=text, start=self._total, end=self._total))

    def extend(self, samples: int) -> None:
        """Count `samples` of audio towards the open span."""
        if not self._spans or samples <= 0:
            return
        self._spans[-1].end += samples
        self._total += samples

    def reset(self) -> None:
        self._spans.clear()
        self._total = 0
        self._heard_before = ""

    def carry(self, heard: str) -> None:
        """Count samples from zero again, keeping `heard` as already said.

        For the rest of a reply that goes out under a new mark (after a spoken
        confirmation): the client counts it from zero, and the words heard before
        stay part of what was heard.
        """
        self._spans.clear()
        self._total = 0
        self._heard_before = heard

    def text_up_to(self, played: int) -> str:
        """The words that had reached the speaker once `played` samples had played.

        Spans that finished before the cut come back whole. The span the cut falls in is
        trimmed to its last whole word and marked "…": half a word teaches the model a
        word she never said. Everything after the cut is left out.
        """
        heard: list[str] = [self._heard_before] if self._heard_before else []
        for span in self._spans:
            if span.samples <= 0:
                continue  # no audio, never heard, wherever its empty range falls
            if span.end <= played:
                if span.text:
                    heard.append(span.text)
                continue
            if span.start >= played:
                break
            partial = _whole_words(span.text, (played - span.start) / span.samples)
            if partial:
                heard.append(f"{partial}…")
            break
        return " ".join(heard)


def _whole_words(text: str, fraction: float) -> str:
    """The leading whole words of `text` that `fraction` of its audio covered.

    Characters stand in for time within one chunk. That isn't exact, since a long word
    isn't said faster than a short one, but a chunk is at most a sentence, and the
    synthesiser reports no word timings. Rounding down to a word keeps the error on the
    honest side: never more than she said.
    """
    cutoff = int(len(text) * max(0.0, min(1.0, fraction)))
    if cutoff >= len(text):
        return text.strip()
    window = text[:cutoff]
    # A cut that lands on a space is already whole words; trimming it again dropped a
    # word she had finished ("of France" became "of").
    if text[cutoff].isspace() or window.endswith(" "):
        return window.strip()
    space = window.rfind(" ")
    return window[:space].strip() if space > 0 else ""
