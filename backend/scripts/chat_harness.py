"""Text-chat harness: type to Veronica and watch tools and citations fire.

Uses the real models (multilingual-e5 and Ollama qwen3) for a manual check of tool
calling and RAG grounding without the UI. It also uses the real memory database, so
anything taught here ("remember that I prefer short answers") carries into voice
calls.

Run from backend/ (Ollama must be up):  uv run python -m scripts.chat_harness
Type a message; 'quit' learns from what was said, then exits. Actions
that need approval (writing a file, opening an app) will time out and be refused,
because the console has no approval prompt.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.core.config import get_settings
from app.core.event_bus import InProcessEventBus
from app.services.chat.learning_queue import LearningQueue
from app.services.chat.learning_worker import LearningWorker, transcript_budget
from app.services.chat.session import ChatSession
from app.services.persona import PersonaService
from app.services.voice.runtime import build_voice_runtime
from app.stores import open_stores


class ConsoleWS:
    """A console stand-in for the chat WebSocket; prints events as they stream."""

    async def send_text(self, text: str) -> None:
        event = json.loads(text)
        kind = event.get("type")
        if kind == "token":
            sys.stdout.write(event["text"])
            sys.stdout.flush()
        elif kind == "tool":
            print(f"\n  · tool {event['name']} -> {event['result']}")
        elif kind == "citations":
            sources = ", ".join(f"[{s['n']}] {s['title']}" for s in event["sources"])
            print(f"\n  · sources: {sources}")
        elif kind == "error":
            print(f"\n  · error: {event['message']}")


async def main() -> None:
    settings = get_settings()
    # The same files the app uses, so the harness sees the real policy and personality.
    data_dir = settings.data_path()
    bus = InProcessEventBus()
    voice = await build_voice_runtime(settings, bus)
    persona = PersonaService(data_dir / "persona.json", bus)
    persona.load()
    # Opened and upgraded as the app does it.
    stores = await open_stores(settings, bus, voice.manager, data_dir)
    memory, kernel, chats = stores.memory, stores.kernel, stores.chat
    session = ChatSession(
        ConsoleWS(),  # type: ignore[arg-type]
        memory,
        voice.llm,
        settings,
        kernel,
        persona,
        chats,
        stores.projects,
    )
    print(f"Veronica text chat (model={settings.llm.model}). Type 'quit' to exit.\n")
    try:
        while True:
            line = await asyncio.to_thread(input, "you> ")
            if line.strip().lower() in {"quit", "exit"}:
                break
            print("veronica> ", end="", flush=True)
            await session._turn(line)
            print("\n")
    finally:
        # The app learns from chat once the model has been free a while; the harness is
        # about to exit, so it drains the queue now.
        worker = LearningWorker(
            LearningQueue(memory.store),
            chats,
            memory.learning,
            voice.llm.background(),
            voice.llm.activity,
            calls_live=lambda: 0,
            model=settings.llm.model,
            quiet_s=0,
            max_chars=transcript_budget(settings.llm.num_ctx),
        )
        while await worker.step():
            pass
        await kernel.aclose()
        await voice.llm.aclose()
        await memory.store.close()
        print("(learning ran; bye)")


if __name__ == "__main__":
    asyncio.run(main())
