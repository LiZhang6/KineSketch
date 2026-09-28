# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation state and tool-call orchestration for the KineSketch agent."""

from __future__ import annotations

import re
import json
from typing import Any

from ..skills import modeling_instructions
from .images import ImageAttachment


SYSTEM_PROMPT = """You are KineSketch, an assistant embedded in FreeCAD.
Help the user inspect and modify the active CAD document. Use the provided tools when
the user asks for a concrete document change. Never claim that a change happened
unless its tool returned success. Prefer simple parametric primitives and concise
answers. Dimensions and coordinates are in millimetres; angles are in degrees.
Do not emit Python code for execution.""" + "\n\n" + modeling_instructions()


def _creation_request(text: str) -> bool:
    # Questions and explicit refusals must not force a document mutation.
    if re.search(r"how\b|what\b|explain\b|do not\b|don't\b|\u5982\u4f55|\u600e\u4e48|\u4ec0\u4e48|\u4ecb\u7ecd|\u89e3\u91ca|\u4e0d\u8981|\u4e0d\u751f\u6210|\u4e0d\u521b\u5efa", text, re.I):
        return False
    return bool(re.search(r"\b(create|generate|build|make)\b|\u751f\u6210|\u521b\u5efa|\u5efa\u6a21|\u5efa\u7acb", text, re.I)
                and re.search(r"\b(crank|model|box|cylinder|part|plate)\b|\u66f2\u67c4|\u6a21\u578b|\u957f\u65b9\u4f53|\u5706\u67f1|\u96f6\u4ef6", text, re.I))


class AgentSession:
    """Maintain a bounded chat history across asynchronous model requests."""

    def __init__(self, max_tool_rounds: int = 6) -> None:
        self.max_tool_rounds = max_tool_rounds
        self._tool_rounds = 0
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._turn_start = 1
        self._creation_requested = False
        self._requested_tool: str | None = None
        self._tool_retry = False

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    @property
    def request_messages(self) -> list[dict[str, Any]]:
        return [self._messages[0], *self._messages[self._turn_start :]]

    @property
    def tool_choice(self) -> str:
        return "required" if self._creation_requested and self._tool_rounds == 0 else "auto"

    @property
    def requested_tool(self) -> str | None:
        return self._requested_tool if self.tool_choice == "required" else None

    def request_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.requested_tool is None:
            return tools
        selected = [tool for tool in tools if tool["function"]["name"] == self.requested_tool]
        if not selected:
            raise ValueError(f"Required modeling tool is unavailable: {self.requested_tool}")
        return selected

    def retry_missing_tool(self) -> bool:
        if self.tool_choice != "required":
            return False
        if self._tool_retry:
            raise RuntimeError("The model did not return a tool call after one retry. "
                               "Check that the selected model supports tool calling.")
        self._tool_retry = True
        self._messages.append({"role": "user", "content": (
            "Execute the requested CAD change using a tool call now, not instructions "
            "or Python code. For create_crank, omit unspecified parameters so the "
            "tool applies prototype defaults. Never claim creation without tool success."
        )})
        return True

    def begin(
        self, user_text: str, document_context: str,
        image: ImageAttachment | None = None,
    ) -> None:
        self._tool_rounds = 0
        self._creation_requested = _creation_request(user_text)
        self._requested_tool = (
            "create_crank" if self._creation_requested
            and re.search(r"\bcrank\b|\u66f2\u67c4", user_text, re.I) else None
        )
        self._tool_retry = False
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
        self._creation_requested = False
        self._requested_tool = None
        self._tool_retry = False
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._turn_start = 1

    def cancel_turn(self) -> None:
        """Close unfinished tool calls without claiming they were executed."""
        turn = self._messages[self._turn_start:]
        completed = {item.get("tool_call_id") for item in turn if item["role"] == "tool"}
        for item in turn:
            for call in item.get("tool_calls", []):
                if call["id"] not in completed:
                    self.add_tool_result(call["id"], json.dumps({
                        "ok": False, "error": "Cancelled by user; action not executed.",
                    }))
        self._messages.append({"role": "assistant", "content": "Conversation stopped by user."})
        self._creation_requested = False
        self._requested_tool = None
        self._tool_retry = False

    def _trim_history(self) -> None:
        if len(self._messages) > 30:
            self._messages = [self._messages[0], *self._messages[-24:]]
