# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation state and tool-call orchestration for the KineSketch agent."""

from __future__ import annotations

from typing import Any

from ..skills import modeling_instructions
from .images import ImageAttachment


SYSTEM_PROMPT = """You are KineSketch, an assistant embedded in FreeCAD.
Help the user inspect and modify the active CAD document. Use the provided tools when
the user asks for a concrete document change. Never claim that a change happened
unless its tool returned success. Prefer simple parametric primitives and concise
answers. Dimensions and coordinates are in millimetres; angles are in degrees.
Do not emit Python code for execution.""" + "\n\n" + modeling_instructions()


class AgentSession:
    """Maintain a bounded chat history across asynchronous model requests."""

    def __init__(self, max_tool_rounds: int = 6) -> None:
        self.max_tool_rounds = max_tool_rounds
        self._tool_rounds = 0
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._turn_start = 1

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    @property
    def request_messages(self) -> list[dict[str, Any]]:
        return [self._messages[0], *self._messages[self._turn_start :]]

    def begin(
        self, user_text: str, document_context: str,
        image: ImageAttachment | None = None,
    ) -> None:
        self._tool_rounds = 0
        self._trim_history()
        self._turn_start = len(self._messages)
        content = f"Current FreeCAD document:\n{document_context}\n\nUser request:\n{user_text}"
        if image is None:
            self._messages.append({"role": "user", "content": content})
        else:
            self._messages.append({"role": "user", "content": [
                {"type": "text", "text": content},
                {"type": "image_url", "image_url": {
                    "url": image.data_url, "detail": "high",
                }},
            ]})

    def accept_assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        stored: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            if not isinstance(tool_calls, list):
                raise ValueError("Assistant tool_calls must be a list")
            stored["tool_calls"] = tool_calls
        self._messages.append(stored)
        return tool_calls

    def add_tool_result(self, call_id: str, result: str) -> None:
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": result}
        )

    def continue_after_tools(self) -> None:
        self._tool_rounds += 1
        if self._tool_rounds >= self.max_tool_rounds:
            raise RuntimeError("Agent exceeded the maximum number of tool rounds")

    def clear(self) -> None:
        self._tool_rounds = 0
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._turn_start = 1

    def _trim_history(self) -> None:
        if len(self._messages) > 30:
            self._messages = [self._messages[0], *self._messages[-24:]]
