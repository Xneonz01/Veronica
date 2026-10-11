"""Print what the black box and the performance log recorded, for a bug report.

    uv run python -m scripts.diagnostics             # summary
    uv run python -m scripts.diagnostics --events    # plus the latest run's problems
    uv run python -m scripts.diagnostics --doctor    # plus a check of this computer
    uv run python -m scripts.diagnostics --json      # the same data as JSON

Everything printed was redacted when it was written (secrets, account names in
paths), but read it before pasting it anywhere public. The check of this computer
(`app/doctor.py`) works with the backend stopped: it only looks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from app import doctor
from app.adapters.model_files import INSTALL
from app.core.config import get_settings
from app.core.version import APP_CHANNEL, APP_VERSION, APP_VERSION_LABEL
from app.telemetry.reader import latest_crash, list_runs, perf_summary, recent_events
from app.telemetry.runtime import telemetry_dir

_EXIT_STATE = {True: "clean exit", False: "did NOT exit cleanly", None: "unknown"}

_CHECKS = {
    "gpu": "graphics card",
    "ram": "memory (RAM)",
    "disk": "disk",
    "llm": "language model",
    "models": "model files",
}
_WORDS = {
    ("gpu", "ok"): "{name}, {total_mib:,} MiB; calls used up to {needs_mib:,} MiB",
    ("gpu", "small"): "{name}, {total_mib:,} MiB: less than the {needs_mib:,} MiB calls used",
    ("gpu", "absent"): "no NVIDIA driver answered (nvidia-smi)",
    ("ram", "ok"): "{total_mib:,} MiB; her backend and Ollama used up to {needs_mib:,} MiB",
    ("ram", "small"): "{total_mib:,} MiB: less than the {needs_mib:,} MiB her backend and "
    "Ollama used in calls",
    ("disk", "ok"): "{free_mib:,} MiB free beside memory.db; an update's copy needs "
    "{needs_mib:,} MiB",
    ("disk", "low"): "{free_mib:,} MiB free beside memory.db: less than the {needs_mib:,} MiB "
    "an update's copy needs",
    ("disk", "full"): "less than 1 MiB free beside memory.db: the disk is full",
    ("llm", "ok"): "{model}, in Ollama at {base_url}",
    ("llm", "unreachable"): "Ollama isn't answering at {base_url} (llm.base_url)",
    ("llm", "model_missing"): "Ollama doesn't have {model}: ollama pull {model}",
    ("llm", "no_tools"): "Ollama says {model} can't use tools, which chat and calls offer it",
    ("models", "ok"): "all {count} on disk",
    ("models", "missing"): f"not on disk: {{models}}; install them with `{INSTALL}`",
}


def collect(*, with_events: bool, with_doctor: bool = False) -> dict[str, Any]:
    """The report as a dict. Paths are left out: they would carry the account name."""
    settings = get_settings()
    directory = telemetry_dir(settings.telemetry)
    runs = list_runs(directory, limit=10)
    report: dict[str, Any] = {
        "version": APP_VERSION,
        "label": APP_VERSION_LABEL,
        "channel": APP_CHANNEL,
        "runs": runs,
        "last_crash": latest_crash(directory),
        "perf": perf_summary(directory / "perf" / "perf.jsonl"),
    }
    if with_events and runs:
        report["latest_run_problems"] = recent_events(directory, str(runs[0].get("run_id")))
    if with_doctor:
        checks = asyncio.run(doctor.examine(settings))
        report["computer"] = [check.model_dump() for check in checks]
    return report


def describe(check: dict[str, Any], base_url: str) -> str:
    """One check in words, for the terminal (the app has words of its own)."""
    words = _WORDS.get((check["check"], check["code"]))
    if words is None:
        return "couldn't be read (the warning above says why)"
    return words.format(**check["detail"], base_url=base_url)


def _print(report: dict[str, Any]) -> None:
    print(f"Veronica {report['version']} ({report['channel']}): {report['label']}")
    print("\nRecent runs, newest first:")
    for run in report["runs"]:
        native = " + native crash log" if run.get("native_crash_log") else ""
        state = _EXIT_STATE.get(run.get("clean_exit"), "unknown")
        print(f"  {run.get('run_id')}  v{run.get('app_version')}  {state}{native}")
    if not report["runs"]:
        print("  (none recorded)")

    crash = report["last_crash"]
    if crash:
        print(f"\nLast crash: {crash.get('event')} in run {crash.get('run_id')}")
    else:
        print("\nLast crash: none recorded")

    print("\nTimings in ms (recent log; interrupted, failed and tool turns left out):")
    for stage, stats in report["perf"].items():
        numbers = f"n={stats['n']:<5} p50={stats['p50']:<9} p95={stats['p95']:<9}"
        print(f"  {stage:<28} {numbers} max={stats['max']}")
    if not report["perf"]:
        print("  (none recorded)")

    if "latest_run_problems" in report:
        print("\nWarnings and errors in the latest run:")
        for event in report["latest_run_problems"]:
            fields = json.dumps(event.get("fields"), ensure_ascii=False)
            print(f"  [{event.get('level')}] {event.get('event')}: {fields}")
        if not report["latest_run_problems"]:
            print("  (none)")

    if "computer" in report:
        base_url = get_settings().llm.base_url
        print("\nThis computer, against what Veronica was measured using:")
        for check in report["computer"]:
            name = _CHECKS.get(check["check"], check["check"])
            print(f"  {check['verdict']:<8} {name:<15} {describe(check, base_url)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise Veronica's local diagnostics.")
    parser.add_argument("--events", action="store_true", help="include the latest run's problems")
    parser.add_argument("--doctor", action="store_true", help="check this computer too")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    options = parser.parse_args()
    report = collect(with_events=options.events, with_doctor=options.doctor)
    if options.json:
        print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    else:
        _print(report)


if __name__ == "__main__":
    main()
