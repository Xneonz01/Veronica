"""What the call probe records, and the percentiles it reports per stage.

`TurnRow` is the metrics row the server sends for each turn (`TurnMetrics.as_dict`);
a test keeps the two in step. p50 is the median; p90 and p95 are nearest-rank, so
each is a turn that really happened rather than a blend of two.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from app.telemetry.percentile import nearest_rank


class TurnRow(BaseModel):
    """One turn's metrics row, as the server sends it (stage durations in ms)."""

    model_config = ConfigDict(extra="ignore")  # the wire message also has a `type`

    ts: str
    user_chars: int
    reply_chars: int
    tool_calls: int
    endpoint_wait_ms: int | None
    stt_final_ms: int | None
    handoff_ms: int | None
    prefetch_ms: int | None
    ttft_ms: int | None
    chunk_wait_ms: int | None
    tts_first_synth_ms: int | None
    first_audio_ms: int | None
    perceived_first_audio_ms: int | None
    total_ms: int | None
    barged_in: bool
    reply_capped: bool = False  # absent from runs saved before the reply cap


class ProbeTurn(BaseModel):
    """One question and its answer: the server's row plus what the probe saw."""

    question: str
    heard: str | None  # the server's transcript of the question
    row: TurnRow
    # From the last question frame sent to the first reply audio received.
    client_first_audio_ms: int | None


class ProbeRun(BaseModel):
    """One call's results, as saved with `--out`."""

    label: str
    version: str | None
    started_at: str
    turns: list[ProbeTurn]
    loop_lag: dict[str, float | int | None] | None


class StageStats(BaseModel):
    """One stage's spread over a set of turns, in milliseconds."""

    n: int
    p50: float
    p90: float
    p95: float
    max: float


# In the order a turn goes through them; the client's own measure comes last.
STAGES = (
    "endpoint_wait_ms",
    "stt_final_ms",
    "handoff_ms",
    "prefetch_ms",
    "ttft_ms",
    "chunk_wait_ms",
    "tts_first_synth_ms",
    "first_audio_ms",
    "perceived_first_audio_ms",
    "total_ms",
    "client_first_audio_ms",
)


def stats(values: list[float]) -> StageStats:
    """Count, median, p90, p95 and max of a non-empty list of durations."""
    ordered = sorted(values)
    return StageStats(
        n=len(ordered),
        p50=round(statistics.median(ordered), 1),
        p90=round(nearest_rank(ordered, 0.90), 1),
        p95=round(nearest_rank(ordered, 0.95), 1),
        max=round(ordered[-1], 1),
    )


def comparable(turns: Iterable[ProbeTurn]) -> list[ProbeTurn]:
    """Turns whose timings mean the same thing: no tool ran, nobody interrupted."""
    return [turn for turn in turns if turn.row.tool_calls == 0 and not turn.row.barged_in]


def summarize(turns: Iterable[ProbeTurn]) -> dict[str, StageStats]:
    """Per stage, over the comparable turns; a stage never measured is left out."""
    kept = comparable(turns)
    summary: dict[str, StageStats] = {}
    for stage in STAGES:
        values = [float(value) for turn in kept if (value := _value(turn, stage)) is not None]
        if values:
            summary[stage] = stats(values)
    return summary


def _value(turn: ProbeTurn, stage: str) -> int | None:
    if stage == "client_first_audio_ms":
        return turn.client_first_audio_ms
    value = getattr(turn.row, stage)
    return value if isinstance(value, int) else None


def report(runs: list[ProbeRun]) -> list[str]:
    """A table per label, over every turn of that label's runs."""
    lines: list[str] = []
    for label in dict.fromkeys(run.label for run in runs):
        labelled = [run for run in runs if run.label == label]
        turns = [turn for run in labelled for turn in run.turns]
        left_out = len(turns) - len(comparable(turns))
        versions = ", ".join(sorted({run.version or "?" for run in labelled}))
        heading = f"{label}: {len(turns)} turns in {len(labelled)} call(s), version {versions}"
        if left_out:
            heading += f"; {left_out} left out (a tool ran or the turn was interrupted)"
        lines.append(heading)
        lines.append(f"  {'stage':<26}{'n':>4}{'p50':>9}{'p90':>9}{'p95':>9}{'max':>9}")
        for stage, row in summarize(turns).items():
            numbers = f"{row.n:>4}{row.p50:>9.0f}{row.p90:>9.0f}{row.p95:>9.0f}{row.max:>9.0f}"
            lines.append(f"  {stage:<26}{numbers}")
        lines.append("")
    return lines
