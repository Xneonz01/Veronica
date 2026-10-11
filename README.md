# Veronica

A personal assistant that runs on your own machine. You can talk to her in a voice
call or type in a chat window. She remembers what you tell her, answers from your
documents, and can work with files and apps, asking before she changes anything.

No cloud and no API keys. Speech runs on your GPU, the language model runs in
[Ollama](https://ollama.com), and everything she knows about you stays in an encrypted
database under `data/`.

Status: pre-1.0, developed and used on Windows.

## Features

- Voice calls with a cloned voice. You can interrupt her mid-sentence.
- Chat with streaming replies, saved conversations, projects and attached documents.
  Answers cite the passage they came from.
- A memory page where you can see, edit, pin, approve or delete what she has learned.
- File and app actions that go through a permission layer: changes need your OK (on
  screen, or out loud during a call) and every decision is logged.
- Voice cloning from 10–15 seconds of speech.
- A tray icon and a global hotkey that opens a small companion window.

## Requirements

- Windows 10 or 11 (Linux and macOS are untested, and encryption at rest is Windows-only)
- An NVIDIA GPU, 16 GB VRAM recommended
- Python 3.12 with [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- Ollama with a model that supports tool calling (`qwen3.5:9b` is the tested default)
- Rust, only for the desktop shell

## Getting started

```bash
cp .env.example .env    # optional

cd backend
uv sync
uv run python -m scripts.install_models       # one-time model download
uv run python -m scripts.seed_default_voice   # default voice for calls
uv run python -m app.runserver                # http://localhost:8000

cd ../frontend
npm install
npm run dev                                   # http://localhost:5173
# or: npm run tauri dev
```

The backend never downloads models on its own; `install_models` is the only step that
does (`--check` lists anything missing). The first call takes a moment while the speech
models load onto the GPU.

Configuration is layered: defaults, `configs/default.yaml`, a profile chosen with
`VERONICA_PROFILE`, `.env`, then environment variables (e.g. `VERONICA_LLM__MODEL`).

## How a call works

```
mic → VAD → faster-whisper (streaming) → LLM via Ollama (tool calling) → Chatterbox TTS (streaming) → speakers
```

Everything streams, and speech starts at the first clause instead of waiting for the
full reply. On the call probe, the time from your last word to her voice is about
2.05 s at the median and 3.01 s at p95. More in [ARCHITECTURE.md](ARCHITECTURE.md).

## Your data

Everything lives under `data/`, which is git-ignored: memory, voice clips, attachments,
personality and local diagnostics. Nothing leaves the machine. Diagnostics strip
secrets and shorten paths before writing; turn them off with `telemetry.enabled: false`.

## Development

```bash
python tasks.py check      # ruff, format, mypy --strict, pytest

cd frontend
npm run lint && npx tsc -b --noEmit && npm test && npm run build
```

The backend tests run on fakes, so they need neither a GPU nor Ollama. Coding agents
should read [AGENTS.md](AGENTS.md) first.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md): how it works
- [ROADMAP.md](ROADMAP.md): status, known bugs, what's next
- [docs/TODO.md](docs/TODO.md): open work
- [CHANGELOG.yaml](CHANGELOG.yaml): release notes
- [SECURITY.md](SECURITY.md): threat model
- [DESIGN.md](DESIGN.md) and [ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md)

## Limitations

- Single user, no authentication. It listens on localhost only and isn't meant to be
  exposed to a network.
- Interrupting her works best with headphones; it relies on the browser's echo
  cancellation.
- Images can be attached but not read yet.

## License

MIT. The models have their own licenses, listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
