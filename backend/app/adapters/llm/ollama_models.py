"""What Ollama has: the models installed, from its `/api/tags`, each with its size on disk
and, where this Ollama lists them, what the model can do (0.33.3 lists "tools",
"completion" and more for each); and the models it has in memory now, from its
`/api/ps`. For the check in Settings (`app/doctor.py`) and a call's room on the card
(`voice/realtime/card_room.py`); chat and calls go through `ollama_client.py`.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

import httpx

from app.adapters.llm.ollama_errors import OllamaUnreachableError
from app.core.config_llm import LlmSettings

_LIST_TIMEOUT_S = 5.0  # a listing is read from disk or memory: no model is loaded to answer
_MIB = 1_048_576


@dataclass(frozen=True, slots=True)
class InstalledModel:
    """A model Ollama has, by the name a request gives it, what it can do, and the size of
    its files; None for what an Ollama doesn't say."""

    name: str
    capabilities: frozenset[str] | None
    size_mib: int | None = None


def installed_models(
    settings: LlmSettings, client: httpx.Client | None = None
) -> list[InstalledModel]:
    """Every model Ollama lists. OllamaUnreachableError when nothing at `base_url`
    answers as Ollama does: it isn't running, or something else holds the address."""
    listed = _listed(settings, "tags", client)
    return [model for entry in listed if (model := _model(entry)) is not None]


def loaded_models(settings: LlmSettings, client: httpx.Client | None = None) -> list[str]:
    """The names of the models Ollama has in memory now; OllamaUnreachableError as for
    `installed_models`."""
    return [
        entry["name"]
        for entry in _listed(settings, "ps", client)
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]


def find(models: list[InstalledModel], name: str) -> InstalledModel | None:
    """The model a request for `name` gets, compared as Ollama compares names."""
    return next((model for model in models if same_model(model.name, name)), None)


def same_model(listed: str, asked: str) -> bool:
    """Whether a request for `asked` gets the model Ollama lists as `listed`."""
    return _canonical(listed) == _canonical(asked)


def on_this_computer(settings: LlmSettings) -> bool:
    """Whether `base_url` is an address on this computer, so that Ollama's models are on
    this computer's card. A name the network gives this computer isn't told from another
    computer's, and counts as elsewhere."""
    host = httpx.URL(settings.base_url).host
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _listed(settings: LlmSettings, path: str, client: httpx.Client | None) -> list[Any]:
    url = f"{settings.base_url.rstrip('/')}/api/{path}"
    timeout = httpx.Timeout(_LIST_TIMEOUT_S, connect=settings.connect_timeout_s)
    try:
        if client is None:
            response = httpx.get(url, timeout=timeout)
        else:
            response = client.get(url, timeout=timeout)
        response.raise_for_status()
        listed = response.json()["models"]
    except (httpx.HTTPError, ValueError, LookupError, TypeError) as error:
        raise OllamaUnreachableError(f"no model list from {settings.base_url}") from error
    if listed is None:  # an Ollama with nothing pulled
        return []
    if not isinstance(listed, list):
        raise OllamaUnreachableError(f"{settings.base_url} doesn't list models as Ollama does")
    return listed


def _canonical(name: str) -> str:
    """`name` without Ollama's default registry and namespace, which its list leaves out,
    ignoring case, and with its tag: `latest` when it has none (measured: "qwen3.5" asks
    for "qwen3.5:latest"). A colon before the last slash is a registry's port, not a tag."""
    canonical = name.strip().casefold()
    for default in ("registry.ollama.ai/", "library/"):
        canonical = canonical.removeprefix(default)
    return canonical if ":" in canonical.rsplit("/", 1)[-1] else f"{canonical}:latest"


def _model(entry: Any) -> InstalledModel | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        return None
    capabilities = entry.get("capabilities")
    return InstalledModel(
        entry["name"],
        frozenset(map(str, capabilities)) if isinstance(capabilities, list) else None,
        _mib(entry.get("size")),
    )


def _mib(size: Any) -> int | None:
    """A size in bytes as whole MiB, rounded down; None for anything but a size."""
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None
    return size // _MIB
