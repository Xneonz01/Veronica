"""Cross-platform task runner for Veronica (the Makefile delegates here).

Usage:
    python tasks.py check          # all backend checks (ruff, format, mypy, pytest)
    python tasks.py lint
    python tasks.py format-check
    python tasks.py typecheck
    python tasks.py test
    python tasks.py fe-build       # frontend type-check + bundle
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# (label, argv, working directory)
Step = tuple[str, list[str], Path]

BACKEND_GATES: list[Step] = [
    ("ruff check", ["uv", "run", "ruff", "check", "."], BACKEND),
    ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."], BACKEND),
    # The scripts and evaluations too: a harness calling a method a refactor removed
    # passed every gate while it only covered `app`.
    ("mypy", ["uv", "run", "mypy", "app", "scripts", "eval"], BACKEND),
    ("pytest", ["uv", "run", "pytest", "-q"], BACKEND),
]

SINGLE: dict[str, Step] = {
    "lint": BACKEND_GATES[0],
    "format-check": BACKEND_GATES[1],
    "typecheck": BACKEND_GATES[2],
    "test": BACKEND_GATES[3],
    "fe-build": ("frontend build", ["npm", "run", "build"], FRONTEND),
}


def _run(step: Step) -> bool:
    """Run one step, streaming its output; return True on success."""
    label, args, cwd = step
    print(f"\n==== {label} ====", flush=True)
    if not cwd.is_dir():
        print(f"error: {cwd} does not exist")
        return False
    exe = shutil.which(args[0])
    if exe is None:
        print(f"error: '{args[0]}' not found on PATH")
        return False
    return subprocess.run([exe, *args[1:]], cwd=cwd).returncode == 0


def main(argv: list[str]) -> int:
    target = argv[0] if argv else "check"
    if target == "check":
        failed = [step[0] for step in BACKEND_GATES if not _run(step)]
        print()
        if failed:
            print(f"FAILED: {', '.join(failed)}")
            return 1
        print("All checks passed.")
        return 0
    if target in SINGLE:
        return 0 if _run(SINGLE[target]) else 1
    print(f"unknown target: {target}")
    print("targets: check, lint, format-check, typecheck, test, fe-build")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
