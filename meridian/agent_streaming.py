"""Bridge OpenAI Agents ``Runner.run_streamed`` (async) to sync iterators for Streamlit ``write_stream``."""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

from agents.agent import Agent
from agents.run import Runner
from agents.stream_events import AgentUpdatedStreamEvent, RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

TContext = TypeVar("TContext")


def stream_agent_text_chunks(
    *,
    agent: Agent[TContext],
    conversation: list[dict[str, Any]],
    context: TContext | None,
    max_turns: int,
    include_tool_hints: bool = True,
) -> tuple[Iterator[str], Callable[[], Any]]:
    """
    Run ``Runner.run_streamed`` on a worker thread with its own event loop; yield text deltas.

    Returns ``(iterator, get_streamed_result)``. Call ``get_streamed_result()`` only after the
    iterator is exhausted; it returns the :class:`agents.result.RunResultStreaming` instance.
    """
    out_q: queue.Queue[str | BaseException | None] = queue.Queue()
    holder: dict[str, Any] = {}

    async def _consume(streamed: Any) -> None:
        async for event in streamed.stream_events():
            if isinstance(event, RawResponsesStreamEvent) and isinstance(
                event.data, ResponseTextDeltaEvent
            ):
                delta = event.data.delta
                if delta:
                    out_q.put(delta)
            elif include_tool_hints and isinstance(event, RunItemStreamEvent):
                if event.name == "tool_called" and getattr(event.item, "type", None) == "tool_call_item":
                    name = getattr(event.item, "tool_name", None) or "tool"
                    out_q.put(f"\n\n*{name}…*\n\n")
            elif isinstance(event, AgentUpdatedStreamEvent):
                out_q.put(f"\n\n*{event.new_agent.name}*\n\n")

    async def _amain() -> None:
        streamed = Runner.run_streamed(
            agent,
            conversation,
            context=context,
            max_turns=max_turns,
        )
        holder["streamed"] = streamed
        await _consume(streamed)
        exc = streamed.run_loop_exception
        if exc is not None:
            out_q.put(exc)

    def _thread_target() -> None:
        try:
            asyncio.run(_amain())
        except BaseException as exc:
            out_q.put(exc)
        finally:
            out_q.put(None)

    def _iter() -> Iterator[str]:
        thread = threading.Thread(target=_thread_target, daemon=True)
        thread.start()
        while True:
            item = out_q.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                thread.join(timeout=120.0)
                raise item
            yield item
        thread.join(timeout=120.0)
        streamed = holder.get("streamed")
        if streamed is not None:
            exc = streamed.run_loop_exception
            if exc is not None:
                raise exc

    def _getter() -> Any:
        if "streamed" not in holder:
            raise RuntimeError("Streaming not finished; exhaust the text iterator first.")
        return holder["streamed"]

    return _iter(), _getter


def iter_static_text_chunks(text: str, *, chunk_chars: int = 12) -> Iterator[str]:
    """Progressive chunks for deterministic assistant lines (e.g. logout prompts)."""
    t = text or ""
    for i in range(0, len(t), chunk_chars):
        yield t[i : i + chunk_chars]
