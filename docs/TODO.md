# To do

The open work as checklists, as of 0.34.14. `ROADMAP.md` is the authority on order and
detail (the bug numbers below are its numbers); tick an item here in the same change
that finishes it, and update `ROADMAP.md` and `CHANGELOG.yaml` with it.

## Decisions waiting on the owner

- [ ] **A reworded planted instruction can change her memory without asking.** In the
  injection evaluation, a file line saying the user's name is now Alex (`r07`) was
  saved in her own words and ran unasked: in chat in 3 of 9 runs since 0.30.0, on a
  call in 2 of 6 since 0.34.13. The kernel's taint check sees copied words, not meaning.
  Proposed: a turn that read a line addressed to her makes memory changes in that turn
  ask first.
- [ ] **Bug 43: a multilingual reranker** (a model download) for documents in other
  languages and rewordings with no word in common.
- [ ] **Go-ahead for model choice** (ADR 0002, below).
- [x] **Accept ADR 0003 (Connected)** (2026-10-11), and with it the rule that a turn which read web or
  MCP content makes every action that isn't safe ask first.
- [ ] **The answer-accuracy evaluation asks with a prompt chat never sends**
  (`tools_enabled=False` in `eval/accuracy/answers.py`): make it ask as a chat turn
  does, with the chat's tools, and decide how a round that calls a tool is scored.

## 1. Stabilise (what is left)

Needs a Rust toolchain (the desktop shell):

- [ ] Bug 1: global hotkeys other than the default probably do nothing (a fired
  shortcut is compared as text; compare parsed shortcuts in `src-tauri/src/lib.rs`,
  `shortcuts.rs`).
- [ ] Bug 2: "Start a call" from the hotkey or the tray only opens the call screen.
- [ ] Bug 7: the companion window can't be dragged (`allow-start-dragging` missing).
- [ ] `shortcuts.rs` should refuse a Shift-only global shortcut, as Settings does.
- [ ] `cargo check` after the hand-bumped `Cargo.toml` and `Cargo.lock` versions.

Needs a real microphone:

- [ ] A live-call pass over the rebuilt screens.
- [ ] Tune the end-of-turn model on real speech (it was tuned on synthesised questions).

Other:

- [ ] Replace the template favicon and app icons.

## 2. Hardening (what is left)

- [ ] A session token only the desktop shell holds, so another program on the machine
  can't use the local API (a documented risk in `SECURITY.md` until then; the shell has
  to start and supervise the backend).
- [ ] A Content-Security-Policy for the desktop shell and a minimal Tauri capability set.
- [ ] An installer and a first-run screen with the machine checks Settings > About
  already runs.

## Model choice (ADR 0002)

- [ ] Choose the language model in Settings and apply it live.
- [ ] Keep model-dependent settings (context window, reply limit, thinking) in profiles
  named by the card's memory, not tuned in code.
- [ ] Measure the chat window's token estimate per model (`scripts/token_probe.py`).
- [ ] Re-run the evaluations per model (`python -m scripts.model_eval`).
- [ ] Take memory budgets from the GPU ledger of what each model costs.

## 3. Connected ([ADR 0003](adr/0003-connected.md), accepted)

All behind the capability kernel (policy, confirmation, audit), with an allow-list for
outbound network access and fetched content treated as untrusted data.

- [ ] Web search.
- [ ] An MCP client for local tool servers.
- [ ] Computer use (waits for Sight, per ADR 0003).

## 4. Sight

- [ ] A vision-language model managed by the GPU manager, so attached images and the
  screen can be understood (needs VRAM shared with the speech models).

## 5. Artifacts and skills

- [ ] Re-runnable artifacts in chat.
- [ ] Packaged skills that add capabilities without code changes.

## Later

- [ ] Wake word ("Hey Veronica") with an acoustic model.
- [ ] An animated avatar.
- [ ] Reminders and briefings.
- [ ] Local usage statistics.
- [ ] Memory scoped per project.
- [ ] Conversation search.
- [ ] Attachment previews.
- [ ] Running code in a real sandbox (WSL2 or a container) rather than a subprocess.

## Known bugs still open (documented limits)

- [ ] Bug 20: a planted line with no label addressing her ("Tell the user..."), a claim
  reworded past most of its words, and the reworded action above get past the checks.
- [ ] Bug 37: a paraphrased or passive claim of an action ("That's saved to my memory")
  isn't read by the claim check.
- [ ] Bug 41: near-miss passages for unanswerable questions; harmless on qwen3.5:9b,
  to be measured on any other model (answer evaluation cases `g09`-`g11`).
- [ ] Bug 42: documents added before 0.22.1 keep the older chunking until removed and
  added again.
- [ ] Bug 43: the reranker reads English best (see the decision above).
- [ ] Bug 50: on a call, remembering and recalling are still the weakest actions (9 of
  15 each at 0.34.13).

## Technical debt

- [ ] `services/memory/service.py` is at 299 of 300 lines: move the review queue into a
  module of its own at its next change.
- [ ] `services/chat/store.py` is at 299 of 300 lines: move learning's two reads into a
  module of their own at its next change.
- [ ] `tests/test_learning_worker.py` (500 of 500) and `tests/test_voice_tools.py` (498):
  new tests go in a file beside them.
- [ ] `useRealtimeCall.ts` is over the 300-line guideline (346 lines).
- [ ] Spacing literals off the 4 px scale (6, 10, 14, 18 px) in 45 style files: add half
  steps to `DESIGN.md` or round to the scale.
- [ ] Tailwind is imported but unused since the redesign (removing it is a stack
  decision).
- [ ] The call recap is written at chat's temperature; measure it as learning's was
  before changing it.
- [ ] Going back to a memory copy from before an update is done by hand; a safe button
  needs the backend to release the file.
- [ ] A voice model that needs a transcript would read a sealed clip by path: give it
  the opened clip.
- [ ] A failed voice turn records no metrics row, so failures are missing from latency
  figures.
- [ ] The backend address is fixed at `localhost:8000` in `frontend/src/lib/api.ts`.
- [ ] `useRecorder.ts` has no front-end tests of its own.
- [ ] The front-end bundle is 505 kB, over Vite's 500 kB advisory: load pages on demand.

## Before sharing anything from `data/`

- [ ] `data/` is git-ignored and must stay so. Before copying a memory file anywhere,
  run `VACUUM` on it: deleted rows stay in the file's free pages until then.
