# Veronica backend

The FastAPI service behind Veronica: the live voice call, chat, memory and documents,
the capability kernel and local diagnostics. How it works is described in
[ARCHITECTURE.md](../ARCHITECTURE.md).

```bash
uv sync
uv run python -m scripts.install_models       # once: downloads the speech and memory models
uv run python -m scripts.seed_default_voice   # once: creates the default voice
uv run python -m app.runserver                # http://localhost:8000
```

It needs Python 3.12, an NVIDIA GPU, and [Ollama](https://ollama.com) running with
`qwen3.5:9b`.

## Scripts

| Command | What it does |
|---|---|
| `uv run python -m scripts.install_models` | downloads the models the server opens (it never downloads itself); `--check` lists what is missing |
| `uv run python -m scripts.seed_default_voice` | creates `veronica-default` from Chatterbox's built-in voice |
| `uv run python -m scripts.chat_harness` | chat with Veronica in the console, with the real models and memory |
| `uv run python -m scripts.voice_tool_gate` | measures what attaching the tool list costs the call's latency |
| `uv run python -m scripts.diagnostics` | summarises recent runs, crashes and timings |

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
```

The tests run on fakes and need neither a GPU nor Ollama.
