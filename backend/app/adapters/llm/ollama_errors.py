"""What Ollama's refusals mean: each one it can give that asking again won't mend is an
error of its own, so every surface can say what to do instead of "try again".

Read from Ollama's own error body, `{"error": "..."}`, and only from it: a wrong address
or something else answering on the port gives no such body, and stays a plain failure.
The texts are Ollama 0.33.3's, measured (bug 45).
"""

from __future__ import annotations

import json

import httpx

# An error body is a line; anything longer isn't one of Ollama's.
_ERROR_BODY_MAX = 4096


class OllamaUnreachableError(RuntimeError):
    """Raised when the Ollama server cannot be reached (never faked as a reply)."""


class OllamaModelMissingError(RuntimeError):
    """Raised when Ollama doesn't have the model asked for. Pulling it is the fix, and
    asking again never is, so it is told apart from a failed turn."""

    def __init__(self, model: str) -> None:
        super().__init__(f"Ollama doesn't have {model!r}: pull it with `ollama pull {model}`")
        self.model = model


class OllamaModelNoToolsError(RuntimeError):
    """Raised when the model can't use tools: 400, `"<model> does not support tools"`.
    Chat and calls always offer them, so asking again never helps; another model does."""

    def __init__(self, model: str) -> None:
        super().__init__(f"{model!r} can't use tools: set llm.model to a model that can")
        self.model = model


class OllamaModelFailedError(RuntimeError):
    """Raised when Ollama couldn't run the model: a 5xx with an error of its own, as a
    runner that stopped as it loaded gives (`"llama-server process has terminated:
    exit status 1"`). Often memory another program holds; freeing it lets the next
    question through."""

    def __init__(self, model: str, error: str) -> None:
        super().__init__(f"Ollama couldn't run {model!r}: {error}")
        self.model = model


class OllamaStreamError(RuntimeError):
    """Raised when a started generation fails or truncates mid-stream.

    Distinct from unreachable: the request succeeded, so the failure must surface as a
    failed turn rather than being silently reported as a complete reply.
    """


async def raise_refusal(response: httpx.Response, model: str) -> None:
    """Raise what Ollama's own error on this failed `response` says, when it is one of
    those above; return for anything else, which the caller raises as it is."""
    error = await _error_of(response)
    if error is None:
        return
    if response.status_code == 404 and "not found" in error:
        raise OllamaModelMissingError(model)
    if response.status_code == 400 and "does not support tools" in error:
        raise OllamaModelNoToolsError(model)
    if response.status_code >= 500:
        raise OllamaModelFailedError(model, error)


async def _error_of(response: httpx.Response) -> str | None:
    """Ollama's own error, `{"error": "..."}`, or None for a body that isn't one."""
    raw = b""
    async for chunk in response.aiter_bytes():
        raw += chunk
        if len(raw) > _ERROR_BODY_MAX:
            return None
    try:
        error = json.loads(raw).get("error")
    except (ValueError, AttributeError):  # not JSON, or not an object
        return None
    return error if isinstance(error, str) else None
