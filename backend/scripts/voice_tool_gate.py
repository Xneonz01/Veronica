"""Latency benchmark: does attaching the voice tool list slow the live call down?

Runs the real Ollama adapter directly (no mic, no TTS) over a fixed script, two
ways on the one model: without tools and with the voice tool list. Reports TTFT, a
first-audio proxy, and tool-selection accuracy.

The first-audio proxy is TTFT plus the time to finish the first sentence, because
that is when the real loop hands a chunk to TTS (`chunker.push` → `_speak`). It is a
lower bound on first audio (synthesis is measured separately in the live metrics),
so compare it with the baseline measured the same way, not with the end-to-end
numbers in `voice_turns.jsonl`.

    uv run python scripts/voice_tool_gate.py
"""

from __future__ import annotations

import asyncio
import statistics as st
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.llm.ollama_client import OllamaLLMClient  # noqa: E402
from app.capabilities import register_capabilities  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.kernel.registry import CapabilityRegistry  # noqa: E402
from app.services.persona import Surface  # noqa: E402
from app.services.persona.defaults import DEFAULT_PERSONALITY, build_prompt  # noqa: E402

# Turns that should not call a tool; their latency is what must not regress.
PLAIN = [
    "Hey, how are you doing today?",
    "What's a good way to keep a small garden alive in a hot summer?",
    "Tell me something interesting about how sound travels underwater.",
    "I'm a bit tired today.",
    "What did we talk about last time?",
]
# Turns that should call a tool, with the tool we expect.
TOOLED = [
    ("What files are in the project folder?", "files_list"),
    ("Read the README and tell me what it says.", "files_read"),
    ("Search my files for anything about the roof.", "files_search"),
]


@dataclass
class Sample:
    ttft_ms: float
    first_sentence_ms: float
    tool: str | None
    text: str = ""


@dataclass
class Run:
    label: str
    plain: list[Sample] = field(default_factory=list)
    tooled: list[tuple[Sample, str]] = field(default_factory=list)


def _sentence_end(text: str) -> bool:
    return any(text.rstrip().endswith(mark) for mark in (".", "!", "?"))


async def one_turn(
    llm: OllamaLLMClient, system: str, user: str, tools: list[dict[str, Any]] | None, model: str
) -> Sample:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    started = time.monotonic()
    ttft = first_sentence = 0.0
    parts: list[str] = []
    tool_name: str | None = None
    async for chunk in llm.stream_chat(messages, tools=tools, model=model):
        if chunk.content:
            if not ttft:
                ttft = (time.monotonic() - started) * 1000
            parts.append(chunk.content)
            if not first_sentence and _sentence_end("".join(parts)):
                first_sentence = (time.monotonic() - started) * 1000
        if chunk.done and chunk.tool_calls:
            tool_name = str(chunk.tool_calls[0].get("function", {}).get("name", "")) or None
    if not first_sentence:  # no sentence boundary (tool round, or one short clause)
        first_sentence = (time.monotonic() - started) * 1000
    return Sample(ttft, first_sentence, tool_name, "".join(parts))


async def measure(
    llm: OllamaLLMClient, label: str, tools: list[dict[str, Any]] | None, model: str
) -> Run:
    system = build_prompt(DEFAULT_PERSONALITY, spoken=True, tools_enabled=tools is not None)
    run = Run(label)
    print(f"\n--- {label} ({model}, {len(tools or [])} tools) ---")
    for user in PLAIN:
        sample = await one_turn(llm, system, user, tools, model)
        run.plain.append(sample)
        called = f" CALLED {sample.tool}" if sample.tool else ""
        print(
            f"  plain  ttft={sample.ttft_ms:6.0f}  1st-sent={sample.first_sentence_ms:7.0f}{called}"
        )
    if tools is not None:
        for user, expected in TOOLED:
            sample = await one_turn(llm, system, user, tools, model)
            run.tooled.append((sample, expected))
            mark = "OK " if sample.tool == expected else "MISS"
            prose = "prose+tool" if sample.text.strip() and sample.tool else "tool only"
            print(
                f"  tool   ttft={sample.ttft_ms:6.0f}  1st-sent={sample.first_sentence_ms:7.0f}"
                f"  {mark} got={sample.tool} ({prose})"
            )
    return run


def report(runs: list[Run]) -> None:
    print("\n=================== SUMMARY ===================")
    baseline = None
    for run in runs:
        ttfts = [s.ttft_ms for s in run.plain]
        firsts = [s.first_sentence_ms for s in run.plain]
        p50_first = st.median(firsts)
        if baseline is None:
            baseline = p50_first
        delta = (p50_first - baseline) / baseline * 100 if baseline else 0.0
        leaked = sum(1 for s in run.plain if s.tool)
        line = (
            f"{run.label:34} plain: ttft p50={st.median(ttfts):6.0f}  "
            f"1st-sent p50={p50_first:7.0f}  ({delta:+5.1f}%)"
        )
        if leaked:
            line += f"  !! {leaked} plain turn(s) called a tool"
        print(line)
        if run.tooled:
            hits = sum(1 for s, exp in run.tooled if s.tool == exp)
            withprose = sum(1 for s, _ in run.tooled if s.text.strip() and s.tool)
            print(
                f"{'':34} tools: {hits}/{len(run.tooled)} correct  |  "
                f"{withprose}/{len(run.tooled)} emitted prose alongside the tool call"
            )


async def main() -> None:
    settings = get_settings()
    llm = OllamaLLMClient(settings.llm)
    registry = CapabilityRegistry()
    register_capabilities(registry, settings)
    tools = registry.schemas(registry.offered(call=True))
    print(f"voice tool list: {[t['function']['name'] for t in tools]}")
    print(f"persona surface: {Surface.voice.value}")

    model = settings.llm.model
    runs = [
        await measure(llm, f"baseline: no tools ({model})", None, model),
        await measure(llm, f"voice tools ({model})", tools, model),
    ]
    report(runs)
    await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
