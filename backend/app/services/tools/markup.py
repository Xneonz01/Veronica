"""A reply written without tools that comes out as a tool call instead of words.

Once a turn's rounds are spent, the model is asked to answer without tools. A model
that still wanted to search wrote the call out as text, in its own format
(`<|tool_call>call:web_search{...}`), and that reached the screen as her answer (found
on another local model in 0.35.0); on a call it would be read aloud. Each family
writes its calls differently and the model is the user's choice, so the start of such
a reply is held back until it can be told from words, and one that opens as a call is
replaced by a plain sentence. Later in a reply, only the markers no prose holds cut it.
Not caught: DeepSeek's `<｜tool▁calls▁begin｜>` and calls written as Python, `[f(x)]`.
"""

from __future__ import annotations

import re

# Markers no prose holds: `<|tool_call>`, Llama (`<|python_tag|>`), Mistral
# (`[TOOL_CALLS]`). Found anywhere in the reply, they end it.
_WRITTEN_CALL = r"<\|tool_call|<\|python_tag\|>|\[TOOL_CALLS\]"
_CALL_INSIDE = re.compile(_WRITTEN_CALL)
# And at its start, Qwen and Hermes (`<tool_call>`) and function tags (`<function=`),
# which an answer about them can mention further in. A bare JSON object isn't a call
# anywhere: an answer can be JSON.
_OPENS_A_CALL = re.compile(rf"\s*(?:{_WRITTEN_CALL}|<tool_call|<function[=_])")
# Enough characters, past any leading space, to read the longest opening above.
_HOLD = 16
# Kept back as a reply streams, in case a marker starts in them: the longest is 14.
_TAIL = 13
NOT_FINISHED = "I couldn't finish looking that up."
# Said to the model, as the app, before it answers without tools, never kept in the
# conversation. Searching a service that was down, then reading pages, another local
# model wrote a call as text in 4 of 9 such answers told nothing, in none of 9 told
# this; qwen3.5:9b answered in words either way, 9 of 9. Sent as a system message
# mid-conversation, it was followed by qwen3.5:9b and two other local models.
LAST_ROUND = {
    "role": "system",
    "content": "You can't use tools any more in this answer. Answer now, in plain words, "
    "from what you already have; if something you tried didn't work, say so plainly.",
}


class MarkupGate:
    """Passes a reply on as it streams, once its start shows it is words, and up to a
    call written into it later."""

    def __init__(self) -> None:
        self._held = ""
        self._decided = False
        self.blocked = False

    def feed(self, text: str | None) -> str:
        """What of `text` (a streamed chunk's, None in a chunk that only ends the reply)
        may be shown or said now."""
        if not text or self.blocked:
            return ""
        self._held += text
        if not self._decided:
            if len(self._held.lstrip()) < _HOLD:
                return ""
            return self._decide() or self._release(_TAIL)
        return self._release(_TAIL)

    def end(self) -> str:
        """What is left to show or say once the reply has ended."""
        if self.blocked:
            return ""
        if not self._decided and (opening := self._decide()):
            return opening
        return self._release(0)

    def _decide(self) -> str:
        """The plain sentence, if the reply opens as a call; nothing otherwise."""
        self._decided = True
        if _OPENS_A_CALL.match(self._held):
            self.blocked, self._held = True, ""
            return NOT_FINISHED
        return ""

    def _release(self, keep: int) -> str:
        """What is held, up to a call written into it, all but the last `keep` characters."""
        if found := _CALL_INSIDE.search(self._held):
            said = self._held[: found.start()].rstrip()
            self.blocked, self._held = True, ""
            return f"{said} {NOT_FINISHED}" if said else NOT_FINISHED
        cut = max(0, len(self._held) - keep)
        out, self._held = self._held[:cut], self._held[cut:]
        return out
