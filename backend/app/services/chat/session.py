"""Text-chat session over /ws/chat, sharing the live call's memory.

No audio, VAD or barge-in: a request/reply turn loop. Each turn assembles the memory
block plus a fenced RAG block (`context.py`), fits them and the conversation into the
model's window (`window.py`), streams the reply from the chat model
(`settings.llm.model`, qwen3.5:9b by default), and runs memory tools and kernel
capabilities through one broker. Every message it stores marks the conversation for
learning, which happens later, once the model is free (`learning_worker.py`).

The turn runs as a task, not inline, so the receive loop stays live while the model
generates. That is what makes Stop work: an abandoned reply stops generating instead
of only disappearing from the screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from app.adapters.llm.ollama_errors import (
    OllamaModelFailedError,
    OllamaModelMissingError,
    OllamaModelNoToolsError,
    OllamaUnreachableError,
)
from app.kernel.taint import Source, Taint
from app.services.chat import context, lifecycle, window
from app.services.chat.projects import ProjectStore
from app.services.chat.replies import stream_reply
from app.services.chat.store import ChatStore, derive_title
from app.services.chat.timing import TurnTimer
from app.services.persona import PersonaService, Surface
from app.services.tools import ToolBroker, ToolOutcome

if TYPE_CHECKING:
    from app.adapters.llm.ollama_client import OllamaLLMClient
    from app.core.config import Settings
    from app.kernel.runtime import KernelRuntime
    from app.services.memory.runtime import MemoryRuntime

logger = logging.getLogger(__name__)


class ChatSession:
    """Drives one text-chat conversation over a WebSocket."""

    def __init__(
        self,
        ws: WebSocket,
        memory: MemoryRuntime,
        llm: OllamaLLMClient,
        settings: Settings,
        kernel: KernelRuntime,
        persona: PersonaService,
        store: ChatStore,
        projects: ProjectStore,
    ) -> None:
        self._ws = ws
        self._memory = memory
        self._llm = llm
        self._settings = settings
        self._store = store
        self._projects = projects
        self._project_id: int | None = None
        self._persona = persona.provider(Surface.text, tools_enabled=True)
        self._model = settings.llm.model  # the deep model — reliable tool-calling
        self._history: list[dict[str, Any]] = []
        self._conversation_id: int | None = None
        # Replies in _history that carried what she read, by where it came from: a cited
        # answer quotes a document, one after a tool call may repeat its result. Later
        # turns read them as untrusted (`kernel/taint.py`).
        self._read: dict[int, Source] = {}
        self._ran: list[ToolOutcome] = []  # what this turn's tool calls produced
        self._turn_task: asyncio.Task[None] | None = None
        self._broker = ToolBroker(kernel.kernel, kernel.registry, actor="veronica:chat")

    async def run(self) -> None:
        try:
            while True:
                message = await self._ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text")
                if not text:
                    continue
                # A malformed frame must not kill the socket (json.loads raises on junk;
                # a bare array raises AttributeError on .get).
                try:
                    payload = json.loads(text)
                except ValueError:
                    logger.debug("dropped a malformed chat frame")
                    continue
                if isinstance(payload, dict):
                    await self._dispatch(payload)
        except WebSocketDisconnect:
            logger.info("ws/chat disconnected")
        finally:
            await self._cancel_turn()

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        action = payload.get("action")
        if action == "open":
            # `project_id` is the surface's current filter: a conversation started from
            # inside a project belongs to it.
            raw_project = payload.get("project_id")
            self._project_id = raw_project if isinstance(raw_project, int) else None
            await self._open(payload.get("conversation_id"))
        elif action == "stop":
            await self._cancel_turn()
            # One acknowledgement, whatever was running. A stopped turn has already
            # said `idle`, and a turn that had just finished said it too; ending the
            # client's discard on `idle` would let either one land on the next turn.
            await self._send({"type": "stopped"})
        elif action in ("message", "retry"):
            if self._turn_task is not None and not self._turn_task.done():
                # The composer disables itself while streaming, but the backend cannot
                # depend on that: two concurrent turns would interleave their tokens.
                await self._send({"type": "error", "kind": "busy", "message": "One at a time."})
                return
            text = str(payload.get("text", ""))
            self._turn_task = asyncio.create_task(self._turn(text, again=action == "retry"))

    async def _open(self, conversation_id: object) -> None:
        await lifecycle.open_conversation(self, conversation_id)

    async def _cancel_turn(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task is None or task.done():
            return
        task.cancel()
        # Awaited, not fire-and-forget: the generation must be gone before the next turn
        # starts, or its tokens arrive interleaved with the new reply's.
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _turn(self, user_text: str, *, again: bool = False) -> None:
        user_text = user_text.strip()
        if not user_text:
            # Whitespace to Python that isn't to JavaScript (U+0085, U+001C) got past
            # the composer, which is now waiting on "thinking".
            await self._send({"type": "state", "state": "idle"})
            return
        # Asked again after a failure: the question is already saved and in the
        # history, and a second copy would show twice, here and on every reload.
        repeat = again and self._history[-1:] == [{"role": "user", "content": user_text}]
        timer = TurnTimer(self._send)
        citations: list[dict[str, Any]] = []
        missed: list[dict[str, str]] = []
        self._ran = []
        try:
            # Inside the try, so a Stop that lands this early still ends on `idle`.
            await self._send({"type": "state", "state": "thinking"})
            # Read once, so the check and the prompt agree; an edit applies next turn.
            persona = self._persona()
            llm, tools = self._settings.llm, window.tool_tokens(self._broker.schemas())
            room = window.prompt_room(llm.num_ctx, llm.reply_tokens, tools)
            # Before anything is saved or searched: the app puts the text back.
            if not await self._fits(persona, user_text, room):
                return
            # Saved before the search, so a Stop during it keeps the question. Inside the
            # try: this touches the database, and the turn runs as a bare task, so an
            # exception outside it would reach nobody and leave "Thinking" on screen.
            await self._ensure_conversation(user_text)
            if not repeat:
                self._history.append({"role": "user", "content": user_text})
                await self._persist("user", user_text)
            scope = await self._project_scope()
            turn = await context.assemble(
                self._memory, self._settings, user_text, document_ids=scope
            )
            missed = turn.caveats()
            recent = self._history[-(self._settings.realtime.history_turns * 2) :]
            blocks = [turn.memory, turn.documents]
            fitted = window.turn_messages(persona, blocks, recent, room)
            messages = fitted.messages
            # Documents that gave way to a long message aren't in the prompt to cite.
            citations = turn.citations if fitted.blocks == len(blocks) else []
            # What she read this turn or repeated from an earlier one, and what the user
            # said in the window the prompt carries: an argument copied from the first
            # and not the second asks first.
            said = [m["content"] for m in messages if m["role"] == "user"]
            read = [*turn.untrusted(fitted.blocks), *lifecycle.carried(self, messages)]
            reply = await stream_reply(
                self._llm,
                self._broker,
                messages,
                model=self._model,
                send=timer.send,
                on_tool=self._record_tool,
                taint=Taint.of(untrusted=read, trusted=said),
                window=window.TurnWindow(messages, room, tools),
            )
        except asyncio.CancelledError:
            # Stop or disconnect. The partial text isn't saved: half a sentence replayed
            # as the answer on reload is worse than no answer.
            await self._send({"type": "state", "state": "idle"})
            raise
        except OllamaModelMissingError as error:
            await self._fail("llm_model_missing", str(error), model=error.model)
            return
        except OllamaModelNoToolsError as error:  # bug 45: another model is the fix
            await self._fail("llm_no_tools", str(error), model=error.model)
            return
        except OllamaModelFailedError as error:  # often memory another program holds
            await self._fail("llm_cant_run", str(error), model=error.model)
            return
        except OllamaUnreachableError:
            await self._fail("llm_unreachable", "Veronica's mind isn't responding right now.")
            return
        except Exception:
            logger.exception("chat turn failed")
            await self._fail("turn_failed", "Something interrupted that reply. Try again.")
            return
        reply = context.strip_unmatched_citations(reply, len(citations))
        if citations or self._ran:
            self._read[len(self._history)] = "document" if citations else "tool_result"
        # What it went without first: it qualifies the whole answer.
        caveats = [*missed, *lifecycle.settle(self, reply, read)]
        await self._persist("assistant", reply, citations=citations, caveats=caveats)
        # Streamed tokens are a live preview; this is the authoritative final text the
        # UI settles on (with invented citation markers removed), and what it said
        # without doing it.
        await self._send({"type": "reply", "text": reply, "caveats": caveats})
        if citations:
            await self._send({"type": "citations", "sources": citations})
        await self._send({"type": "state", "state": "idle"})
        timer.log(cited=len(citations))

    async def _project_scope(self) -> list[int] | None:
        """Documents this turn may retrieve from; None (outside a project) means all."""
        if self._project_id is None:
            return None
        return await self._projects.document_ids_for(self._project_id)

    async def _ensure_conversation(self, first_message: str) -> None:
        if self._conversation_id is not None:
            return
        conversation = await self._store.create(derive_title(first_message))
        self._conversation_id = conversation.id
        if self._project_id is not None:
            await self._projects.assign(conversation.id, self._project_id)
        await self._send(
            {
                "type": "conversation",
                "conversation_id": conversation.id,
                "title": conversation.title,
            }
        )

    async def _persist(self, role: str, content: str, **extra: Any) -> None:
        if self._conversation_id is None:
            return
        try:
            await self._store.add_message(self._conversation_id, role, content, **extra)
        except Exception:  # a storage failure must not cost the user the reply
            logger.exception("could not persist a chat message")

    async def _fits(self, persona: str, question: str, room: int) -> bool:
        """Whether the question fits the window beside the persona; if not, says why."""
        refused = window.refusal(persona, question, room)
        if refused == "message_too_long":
            await self._fail("message_too_long", "That message is too long to read at once.")
        elif refused == "context_too_small":
            # A setting, not the message: nothing would fit. `LlmSettings` guards a
            # window set too small and the persona service a personality saved too long;
            # one saved before either, or a long tool list, can still get here.
            llm = self._settings.llm
            logger.warning(
                "the chat window leaves no room for a message: num_ctx=%d, reply=%d, "
                "room after tools=%d, system prompt=%d",
                llm.num_ctx,
                llm.reply_tokens,
                room,
                window.message_tokens({"content": persona}),
            )
            await self._fail("context_too_small", "There's no room left for a message.")
        return refused is None

    async def _record_tool(self, name: str, outcome: ToolOutcome) -> None:
        """Persist a tool result so the transcript shows actions as well as replies."""
        self._ran.append(outcome)
        await self._persist("tool", outcome.shown, tool_name=name, tool_ok=outcome.ok)

    async def _fail(self, kind: str, message: str, **detail: str) -> None:
        """Said as a kind the app has words for, with the values those words need."""
        await self._send({"type": "error", "kind": kind, "message": message, **detail})
        await self._send({"type": "state", "state": "idle"})

    async def _send(self, payload: dict[str, Any]) -> None:
        try:
            await self._ws.send_text(json.dumps(payload))
        except Exception as exc:  # a dead socket ends the turn, not the server
            logger.debug("chat send failed: %r", exc)
