# AGENTS.md: how to work on Veronica

Read this first, every session, whichever coding agent you are. It holds the rules this
project has been built by and the way the work has been done. `ENGINEERING_STANDARDS.md`
has the full coding rules, `DESIGN.md` the interface rules; this file is the short,
binding version plus the working method.

## What Veronica is

A local-first, private, agentic personal assistant for the desktop: a fast local voice
call (cloned voice, barge-in), a chat with documents, citations and projects, a
four-tier editable memory, a capability kernel that asks before it acts and keeps an
audit log, and an editable personality. One brain shared by voice and text, running
entirely on the user's own machine. It is meant for people who prefer local models, so
**the language model must be switchable**: nothing may assume one model or one graphics
card size. Model-specific values live in settings and profiles and are measured per
model.

Not part of the product, and not to be built or prepared for: vision cameras, PTZ, face
recognition, IoT, ESP32, MQTT, household roles, multi-user RBAC, JWT auth. If a document
still mentions them, it is stale: say so.

## The documents, in order of authority

1. `ARCHITECTURE.md`: the living truth of what exists now (modules, contracts, data
   model, security model). Update it in the same change whenever the code it describes
   changes.
2. `ROADMAP.md`: status per area, the numbered known bugs, the order of what comes next,
   technical debt, the release checklist. Update it when something ships or a bug is
   found or fixed.
3. `docs/TODO.md`: the open work as checklists, drawn from the roadmap.
4. `CHANGELOG.yaml` and `backend/app/core/version.py`: the patch notes (shown in
   Settings > About) and the one version number.
5. `docs/adr/`: decisions that are made and why.

If a request conflicts with `ROADMAP.md` or with this file, stop and ask the human.

## Rules that are not negotiable

- **Work the current task only.** Don't build ahead or scaffold later roadmap steps.
- **No god-files.** Backend `app/`, `eval/` and `scripts/` files: 300 lines at most.
  Tests and front-end files: 500. One responsibility per module; split a module that
  outgrows its job rather than extending it. `tests/test_architecture.py` enforces it.
- **No AI slop.** Clear names, small functions, comments that say *why*, no emoji in
  code, no speculative abstraction, no "might need it later" code.
- **Never invent.** Don't guess a library API, a signature, a path, a config key or a
  version. Read the installed package or the code; run it when reading can't settle
  it; otherwise ask. A confident wrong guess is worse than a question.
- **No fake-done.** No `pass`, `TODO` or `NotImplementedError` presented as finished, no
  placeholder data posing as real. An intentional stub is written in `ROADMAP.md` and
  under `known` in the patch notes.
- **Typed and validated.** Full type hints, mypy strict; Pydantic v2 for structured data
  and config; TypeScript strict.
- **Adapters and the kernel are sacred.** Models sit behind provider interfaces
  (`STTProvider`, `LLMProvider`, ...) and load only through the GPU model manager.
  Veronica never drives a file, an app or the computer directly: she requests a
  capability and the kernel runs it with policy, confirmation and audit.
- **Never spam the UI.** The backend emits typed events; the UI gets rate-limited state
  snapshots. Raw high-frequency data never goes over the state channel.
- **Live-reactive state.** A setting changed from the UI or by Veronica applies at once
  to the running pipelines and every open screen, both ways. Never require a reload or
  a restart.
- **Respect the graphics card.** Models load and unload through the one GPU model
  manager. Before running anything on the GPU, check `nvidia-smi`: the card is shared
  with other programs.
- **Telemetry through the accessors** (`current_perf().log(...)`, `time_stage()`,
  `current_blackbox().record(...)`). Perf rows carry names, counts and short labels,
  never argument values, file contents, transcripts or paths.
  `tests/test_telemetry_canary.py` must keep passing.
- **The repository is public-facing and its owner stays anonymous.** Never write a real
  name, email, username, machine path, serial number or location into code, tests,
  docs or patch notes. Test fixtures use made-up people (Rowan, Morgan, Sam) and
  obviously fake tokens (`sk-proj-FAKE...`). `data/`, `.env`, `.claude/` and private
  planning notes are git-ignored; keep them that way. Never commit secrets.
- **Version control is the humans' job.** Don't commit, push, rebase or create branches
  unless a person asks for it in the conversation.

## Engineering standard

Work as a senior engineer would. The architecture keeps growing, so the first duty is
not to break what works.

- **Modular.** Dependencies point one way: `api` -> `services` -> `adapters`/`core`, and
  `core` imports no service. A new concern gets a new module.
- **Efficient.** No work the result doesn't need, nothing blocking the event loop, no
  polling where an event exists, bounded time and memory for anything fed from outside.
  Measure before optimising and say what was measured.
- **Maintainable.** Code a stranger can change safely a year from now.
- **Don't break the code.** Existing behaviour stays unless the task is about it.

## Definition of done

1. The change does exactly what was asked.
2. Gates are green: `python tasks.py check` (ruff, format, mypy strict, pytest) and,
   when the front end is touched, `npm run lint`, `npx tsc -b --noEmit`, `npm test` and
   `npm run build` in `frontend/`.
3. New or changed code has tests that exercise behaviour, not just imports.
4. `ARCHITECTURE.md`, `ROADMAP.md` and `docs/TODO.md` are updated where the change
   touches what they describe.
5. Shipped changes go into `CHANGELOG.yaml` (`new`, `improved`, `fixed`, `known`), in
   product language with measured numbers only. A release sets the same version in
   `version.py`, `pyproject.toml`, `package.json`, `Cargo.toml` and `tauri.conf.json` and
   refreshes the lock files; `tests/test_version_drift.py` fails on any mismatch. Before
   1.0, features bump the minor version and fix-only releases the patch version.

## How the work has been done (keep doing it this way)

- **Read before writing.** Read the module you change and the ones that call it, and
  the installed library you use.
- **Measure first, then decide.** When a fix depends on how the model behaves (prompt
  wording, a threshold, a gate), measure the current state before changing it, with the
  evaluations in `backend/eval` (`python -m scripts.model_eval`, `scripts/retrieval_eval.py`,
  `scripts/call_probe.py`). Compare arms side by side on the same cases, interleaved,
  several runs each; read the answers by hand, not only the score. Don't tune a gold
  case to fit an answer you have already seen without saying so.
- **Results are records.** Files in `backend/eval/results` are never overwritten. A run
  made before a fix it doesn't reflect is moved aside, not edited.
- **Check that tests can fail.** After writing tests for a change, break the code on
  purpose (one mutation at a time: drop a condition, flip a comparison, skip a step) and
  confirm a test fails each time. Restore the file exactly afterwards.
- **Review every release.** Before calling a release done, have it reviewed by a fresh
  pair of eyes (another agent or a person) for correctness, false positives, telemetry
  leaks, doc accuracy and these rules; fix every finding or state why not, and
  re-measure what the fix touches.
- **Check the UI in a browser.** For anything the user sees, run the app against a
  scratch data folder (never the real `data/`), take screenshots, and check every
  state: loading, empty, offline, permission denied, failed.
- **Never a raw error.** The backend returns typed error codes and safe messages; the
  front end maps each code to calm, plain copy. Every surface handles loading, empty,
  offline, permission-denied and failed states. Errors are announced to screen readers.
- **Design is law.** Every component follows `DESIGN.md` tokens: no hardcoded colour,
  spacing or shadow. Motion is GPU-accelerated and respects `prefers-reduced-motion`.
  English only.
- **Never lose data.** Don't delete files permanently: move them aside or to the
  Recycle Bin. Never point a test or a scratch backend at the real `data/` folder.

## Tech stack (locked; changing it needs an ADR)

Python 3.12, FastAPI (async), `uv`, Pydantic v2, pytest, ruff, mypy. React 19 with
TypeScript, Vite, packaged in Tauri 2. SQLCipher (SQLite, encrypted; ADR 0001) with
sqlite-vec and FTS5. Models behind adapters: faster-whisper large-v3-turbo (speech to
text), a tool-calling model through Ollama (qwen3.5:9b is the measured default),
Chatterbox Turbo (speech), smart-turn v3.1 (end of turn), multilingual-e5-base
(embeddings), ms-marco MiniLM-L-6 (reranking). Not allowed: XTTS (licence), a separate
intent-router model (use native tool calling), Redis or NATS before the app is
multi-process.

## When unsure

Stop and ask. Questions are cheap; a wrong build is expensive.
