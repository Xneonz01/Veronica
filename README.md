# Veronica

A personal assistant that runs entirely on your own computer. You can talk to it in a
real-time voice call or type to it in a chat window; it remembers what you tell it,
reads the documents you give it, and can work with files and apps on your machine,
asking first before it changes anything. There are no API keys and no cloud services:
the speech models run on your GPU, the language model runs in a local
[Ollama](https://ollama.com), and everything it knows about you is in a SQLite file you
can open and edit.

It is a desktop app (a Tauri shell around a React interface) with a Python backend.
It is pre-1.0 and has been developed and used on a single Windows machine.

## What it does

- **Voice calls.** Continuous, hands-free conversation with a cloned voice. You can talk
  over it at any point and it stops immediately.
- **Chat.** Streaming replies, conversations that persist, projects, and documents you
  can attach and ask about. Answers cite the passage they came from.
- **Memory you can see.** Everything it learns about you is listed on the Memory page,
  where you can edit, pin, delete or approve it. Anything that looks like a password or
  an access code is refused, never stored.
- **Actions with permission.** It can read, search and write files in folders you allow
  and open apps you allow. Anything that changes something asks first (on screen, or
  out loud during a call), and every decision goes into an append-only log.
- **Voice cloning.** Record or upload about 10–15 seconds of speech and it speaks in that
  voice.
- **Always one keystroke away.** A global hotkey opens a small companion window from any
  app; it lives in the tray and can start with Windows.

## Measured

Over 104 real voice turns recorded during development:

| | p50 | p90 | p95 | range |
|---|---|---|---|---|
| Time to first token | 406 ms | 510 ms | 587 ms | 359 – 2,015 ms |
| Time to first audio | 1,391 ms | 2,865 ms | 3,855 ms | 797 – 7,719 ms |

Both are timed from the moment the final transcript is ready. What you hear comes later:
before that point Veronica waits for a little over 400 ms of silence to decide you've
finished, then runs a final transcription pass. Each turn now also records those stages,
from your last word to the first audio (see
[ARCHITECTURE.md §7.9](ARCHITECTURE.md#79-latency-metrics)); a baseline with them is
next. The slow tail includes first turns after a cold start and a few runs from before
calls switched to the faster 8B model.

Attaching the tool list to the call costs nothing measurable (391 ms to first token with
it, 406 ms without), so the model's own function calling chooses tools; there is no
separate intent router. Speech recognition and synthesis together use about 6.8 GB of
VRAM.

## How a spoken turn works

```
microphone → voice activity detection (Silero) → speech recognition (faster-whisper, streaming)
                                                        ↓
                                    Qwen3 via Ollama, with native tool calling
                                                        ↓
                clause chunker → speech synthesis (Chatterbox, streaming) → speakers
```

Recognition, generation and synthesis all stream, and synthesis starts at the first short
clause rather than the end of the reply. Interrupting her bumps a generation counter,
cancels the reply and drains the audio queue in one step, so nothing already
interrupted gets played. The details are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

- Windows 10 or 11 (it should run on Linux and macOS, but hasn't been tested there, and
  her memory and voice clips are encrypted at rest only on Windows, whose DPAPI keeps
  the key)
- An NVIDIA GPU with 16 GB of VRAM (smaller cards may work with smaller models)
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer
- [Ollama](https://ollama.com) running locally, with `qwen3.5:9b` pulled
- Rust (stable), only if you want to run or build the desktop shell

## Getting started

From the repository root:

```bash
cp .env.example .env              # optional; the defaults work

# Backend
cd backend
uv sync
uv run python -m scripts.install_models       # downloads the speech and memory models, once
uv run python -m scripts.seed_default_voice   # creates the default voice (needed for calls)
uv run python -m app.runserver                # http://localhost:8000

# Front end, in a second terminal
cd frontend
npm install
npm run dev                                   # http://localhost:5173

# Or the desktop app instead of the browser
npm run tauri dev
```

The backend never downloads anything itself: it opens its models from disk only, and
`scripts.install_models` is the one step that fetches them (`--check` lists what is
missing). The first call after starting the backend takes a while, because the speech
models are loaded onto the GPU when a call starts.

Settings are layered: built-in defaults, then `configs/default.yaml`, then a profile
(`configs/profile.<name>.yaml`, chosen with `VERONICA_PROFILE`), then `.env`, then
environment variables such as `VERONICA_LLM__MODEL=qwen3:8b`.

## Your data

Everything Veronica stores lives under `data/` (git-ignored): the memory database, voice
reference clips, chat attachments, the personality text, and local diagnostics. Nothing
is sent anywhere.

The diagnostics are a black box that records each run (errors, crashes, whether it
shut down cleanly) and a performance log of pipeline timings. Values that look like
secrets are removed before anything is written, and file paths are shortened so they
show neither your account name nor where the project lives. To
see what was recorded, for example to attach to a bug report:

```bash
cd backend
uv run python -m scripts.diagnostics --events
```

Set `telemetry.enabled: false` in the config to turn it off.

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the backend works, module by module |
| [ROADMAP.md](ROADMAP.md) | What works, what's broken, what's next |
| [docs/TODO.md](docs/TODO.md) | The open work as checklists |
| [AGENTS.md](AGENTS.md) | Rules and working method for coding agents (read first) |
| [CHANGELOG.yaml](CHANGELOG.yaml) | Patch notes for every release (also in Settings → About) |
| [SECURITY.md](SECURITY.md) | Threat model and how to report a problem |
| [DESIGN.md](DESIGN.md) | The interface's design tokens |
| [ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md) | Coding rules for contributors |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | Licences of the models and libraries |

## Layout

```
backend/app/
  adapters/      model adapters: speech recognition, language model, speech synthesis, embeddings
  api/           REST routes and WebSocket endpoints
  capabilities/  what she is allowed to do: files and apps
  core/          configuration, event bus, GPU model manager, version, changelog
  kernel/        the capability kernel: policy, confirmation, audit
  services/      the voice call, chat, memory and documents, personality, tools
  telemetry/     the black box and the performance log
backend/tests/   pytest suite (runs without a GPU)
backend/scripts/ default voice, console chat, latency benchmark and call probe, diagnostics report
configs/         default settings and hardware profiles
frontend/src/    React interface
frontend/src-tauri/  Tauri shell: tray, global hotkeys, companion window
```

## Development

```bash
python tasks.py check     # backend: ruff, ruff format --check, mypy --strict, pytest

cd frontend
npm run lint              # oxlint
npx tsc -b --noEmit
npm test                  # vitest
npm run build
```

No module in `backend/app` is longer than 300 lines, and the backend test suite runs on
fakes, so it needs neither a GPU nor Ollama. See [ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md).

## Known limitations

- Single user and no authentication. It binds to localhost and checks the `Origin` of
  every request, but it is not meant to be exposed to a network.
- When you interrupt her, the transcript records what was sent to the speech engine,
  not exactly what reached your speakers.
- Barge-in relies on the browser's echo cancellation: reliable with headphones, less so
  with loud speakers.
- Images can be attached but not understood yet; there is no vision model.

The full list, including known bugs, is in [ROADMAP.md](ROADMAP.md).

## Licence

MIT, see [LICENSE](LICENSE). The models Veronica downloads have their own licences; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
