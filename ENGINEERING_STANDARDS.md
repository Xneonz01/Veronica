# Engineering standards

How code in this repository is written. The aim is a codebase one person can keep in
their head: small modules, honest behaviour, tests that prove what they claim.

## Principles

1. **Boring and reliable beats clever.** Prefer the obvious solution.
2. **Small, single-purpose units.** Many well-named small files are easier to change than
   a few large ones. If a module needs "and" to describe it, split it.
3. **Show the truth.** Code and interface report real state, or say they don't know. No
   fake readiness, no placeholder data presented as real, no stub presented as done.
4. **Build in small steps.** One module, its tests, green gates, then the next.
5. **Write like a person.** Comments explain why, not what. No boilerplate, no emoji, no
   catch-all error handling that hides failures.

## Growing without breaking

The architecture keeps growing, so every change is made to leave what works working.

- Existing behaviour stays unless the change is about it. A change that alters it says
  so in its tests and in the patch notes.
- A new concern gets a new module with a narrow interface. A module outgrowing its one
  job is split, not extended.
- No work the result doesn't need: no repeated I/O in a loop, nothing blocking the event
  loop, no polling where an event exists, and bounded time and memory for anything fed
  from outside. Measure before optimising, and state what was measured.
- Verify, don't recall: read the code being changed and the installed library before
  calling it, and run it when reading can't settle a question.

## Python

- Python 3.12, managed with `uv` (`uv add`, `uv run`). No ad-hoc `pip install`.
- Full type hints and `from __future__ import annotations` in every module; `mypy --strict`
  must pass. Avoid `Any` unless there is no reasonable alternative.
- Pydantic models for structured data crossing module boundaries: config, events, API
  bodies, capability arguments and results. No loose dicts between modules.
- The backend is async. Never block the event loop: CPU- or GPU-heavy work goes to a
  thread (`asyncio.to_thread`) or through the GPU model manager. No `time.sleep` in async
  code.
- Raise specific exceptions and catch narrowly. Never a bare `except:`, never swallow an
  error without logging it. Degrade gracefully, and say so.
- Logging with `logger = logging.getLogger(__name__)`. No `print` in library code.
- Names say what a thing is or does. `snake_case` for functions and variables,
  `PascalCase` for classes, `UPPER_SNAKE` for constants.
- A one-line docstring on every public module, class and function; more only when the
  reason behind the code isn't obvious.

## Size limits

- Files: aim for under 300 lines; 500 is the hard limit. Past 300, split by
  responsibility, not by line count.
- Functions: aim for under 40 lines.
- Classes: one clear responsibility. A "Manager" doing five things is five classes.

## Structure

- `api/` routes are thin: validate input, call a service, return. No business logic.
- Dependencies point inward: `api` → `services` → `core`. No circular imports.
- Models live behind the adapter interfaces in `adapters/base.py`. Services depend on the
  interface, never on a particular model, and every GPU model is loaded through the GPU
  model manager.
- Anything that acts on the machine is a capability run by the kernel: policy,
  confirmation where needed, and an audit row. The model proposes; the kernel decides.
- Services publish typed events on the event bus. Raw high-frequency data (audio) never
  goes over the bus or the event lane.
- Settings and state changes apply live, to the running pipelines and every open window,
  without a restart.

## Errors the user sees

- The backend returns typed errors: a stable `code` plus safe text. The front end maps
  codes to copy in one place (`frontend/src/lib/api.ts`); raw server text, stack traces
  and status codes never reach the screen.
- Every screen handles loading, empty, offline, permission-denied and failed states. A
  failed action always says so; it never just reverts silently.

## Interface

- `DESIGN.md` defines the tokens. Use them; if something is missing, add it there first.
- Honour `prefers-reduced-motion`, keep text at WCAG AA contrast, and make every control
  keyboard-reachable.

## Tests

- `pytest` with `pytest-asyncio`. Every module gets tests that exercise behaviour (the
  normal path and at least one failure path), not just imports.
- No real models or network in unit tests: use the fakes in `tests/fakes.py`. Freeze
  clocks instead of widening assertions.
- Safety behaviour is tested first-class: confirmation gates, policy denials, path
  containment, redaction, cancellation.
- Name a regression test after the failure it prevents.
- Test data is made up. Never put real names, paths, addresses or credentials in
  fixtures; token-shaped fake secrets should be obviously fake.
- The structural rules on this page are enforced by `tests/test_architecture.py`, and
  anonymity and git-ignore coverage by `tests/test_repository.py`. An exception to an
  architecture rule is added to the test with its reason, never worked around.

## Gates

A change is done when all of these pass:

| Check | Command |
|---|---|
| Lint and format | `uv run ruff check .` and `uv run ruff format --check .` |
| Types | `uv run mypy app` |
| Tests | `uv run pytest -q` |
| Front end | `npm run lint`, `npx tsc -b --noEmit`, `npm test`, `npm run build` |

`python tasks.py check` runs the backend ones.

## Dependencies

- Don't trust version numbers from memory. Add a dependency with `uv add` or `npm install`
  and let the lock file record it.
- Check that an API exists in the installed package before using it.
- Keep the dependency set small, and prefer permissive licences; check a model's licence
  before adopting it (see `THIRD_PARTY_NOTICES.md`).

## Telemetry and privacy

- Diagnostics stay on the machine. The black box redacts on write; the performance log
  does not, so perf rows carry names, counts and short labels only, never argument
  values, file contents, transcripts or paths.
- Don't put host names, user names or absolute paths in telemetry fields. The redactor
  shortens paths in tracebacks and log messages, but it is a safety net, not the plan.
- `tests/test_telemetry_canary.py` must keep passing: a secret passed to any capability
  must not appear in telemetry, the audit log or the logs.

## Versions and patch notes

- `backend/app/core/version.py` is the one place the version is written; the newest entry
  of `CHANGELOG.yaml` and every manifest must match it (`tests/test_version_drift.py`).
- Before 1.0: a release that adds features bumps the minor version, a fix-only release
  bumps the patch version.
- Patch notes describe shipped changes in product language, quote measured numbers only,
  and list known issues honestly.

## Commits

- Conventional prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- One logical change per commit, committed with the gates green.

## Avoid

- A module that orchestrates everything.
- Keyword or regex routing of natural-language intent; the model's tool calling does
  that.
- Business logic in routes.
- Loading a model outside the GPU model manager.
- Silent `except` blocks.
- Stubs or placeholder data presented as finished.
- Building ahead of what the current change needs.
