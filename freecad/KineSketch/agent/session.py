# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation state and tool-call orchestration for the KineSketch agent."""

from __future__ import annotations

import json
import re
from typing import Any
from .images import ImageAttachment


SYSTEM_PROMPT = """You are KineSketch, an assistant embedded in FreeCAD.
Help the user inspect and modify the active CAD document. Use the provided tools when
the user asks for a concrete document change. Never claim that a change happened
unless its tool returned success. Prefer simple parametric primitives and concise
answers. Dimensions and coordinates are in millimetres; angles are in degrees.
The supplied functions are the available local FreeCAD tools; do not search for
other tools. Use the internal object name from a successful creation result for
later operations. Correct recoverable tool errors using the returned results and
retry; report any remaining failure accurately. If no supplied tool supports the
requested operation, explain that limitation instead of claiming it was performed.
For creation requests, call modeling tools instead of promising future work.
For images, identify supported geometry first. Use readable dimensions; when
dimensions are missing, choose feasible defaults in millimetres and report those
assumptions. Do not claim exact reproduction of an unscaled image. Reasoning text
and code blocks are never executed CAD operations. For follow-ups such as Continue,
use the prior request and successful tool results to resume unfinished work.
Do not recreate completed objects unless the user explicitly asks for duplicates.
Use the current document's dimensions and placement rather than guessing them.
A box's x/y/z is its minimum corner, not the centre of its bottom face.
Use boolean_operation for real cuts and fusions: a cylinder adds material, not a
hole. For a rectangular frame, cut a smaller box through an outer box, then cut
cylinders through the corner pads for holes. Cutting tools must extend beyond
both faces. Use each successful result's name as the next cut's base. Operands
are preserved and hidden, not deleted; do not confuse hidden dependencies with
unwanted duplicate geometry. Do not claim objects were deleted without a tool.
Choose one supported construction plan and call tools. Do not keep restating
the same plan or reasoning about a capability that the supplied tools support.
If a necessary operation is genuinely unsupported, explain it once and stop."""

SLIDER_CRANK_PROMPT = """For a rail-guided, zero-offset slider-crank with a base, crank, connecting rod
and slider, translate the user's text into your own explicit additive feature
plan and call build_slider_crank_from_plan. The tool builds exactly the boxes
and cylinders you specify; it does not load prebuilt CAD parts. Use a base plate,
two parallel rails and a pivot for ground; a bar and two end bosses each for
the crank and connecting rod; and a slider body that fits between the rails.
Work in each part's local coordinates. Crank pivot and pin centres are local
(0,0,0) and (crank_radius_mm,0,0), rod end centres are local (0,0,0) and
(rod_length_mm,0,0), and the ground pivot and slider pin centres are at their
local origins. Put solid material at every named joint point. A box origin is
its MINIMUM corner, never its centre; a cylinder origin is the centre of its
bottom face. Design the ground with its plate BELOW z=0 and spanning both
negative and positive y. Put its pivot cylinder at x=0,y=0, ending at z=0.
Place two rails at positive and negative y, fully on and touching the plate;
leave a clear central gap wider than the slider. Let rails reach beyond the
slider's full travel x=rod_length_mm +/- crank_radius_mm. Centre crank and
rod bars on y=0 and overlap their end bosses at x=0 and x=their length.
Centre the slider body at local x=0,y=0,z=0. All additive features within
each part must touch or overlap into one solid. Infer reasonable unspecified
thicknesses and clearances; do not invent a different mechanism.
This geometry skill and the Assembly/Kinematic skills support this topology only.
If the tool rejects a feature plan, correct its reported issue and retry.
Use replay_slider_crank for a later replay request. Do not
approximate this mechanism with unrelated standalone primitive tool calls and
do not emit Python code for execution. When visual inspection is useful, call
capture_viewport. Its image arrives in the next user message. Describe only
what is visible and distinguish visual observations from CAD/solver tool results."""


CREATION_TOOLS = frozenset({"create_box", "create_cylinder", "boolean_operation", "build_slider_crank_from_plan"})
HISTORY_TURNS = 8
HISTORY_CHARACTERS = 60000


def _requests_creation(text: str) -> bool:
    lowered = text.lower()
    if any(word in lowered for word in ("如何", "怎么", "怎样", "how to", "how do", "how can")):
        return False
    if re.search(r"(?:不要|无需|别|不需要)\s*(?:生成|创建|构建|建模|制作)|"
                 r"(?:do not|don't|never)\s+(?:create|generate|build|model)", lowered):
        return False
    return bool(re.search(r"生成|创建|构建|建模|制作|装配|仿真|演示|\b(?:create|generate|build|simulate)\b|"
                          r"(?:^|please\s+)model\s|\bmodel\s+(?:a|an|the|this|that)\b", lowered))


VIEWPORT_TOOL_PROMPT = (
    "FreeCAD viewport screenshot requested by capture_viewport. Inspect this "
    "image for the current task. Report visible evidence and uncertainty; use "
    "tool results for dimensions, hidden geometry, and solver status."
)

POST_ACTION_REVIEW_PROMPT = (
    "Experimental visual check of the FreeCAD viewport after the actions above. "
    "Compare the visible result with the original user request and tool results. "
    "State whether the visible result appears consistent, list concrete visible "
    "problems, and say what the screenshot cannot verify. Do not call tools."
)


class AgentSession:
    """Maintain a bounded chat history across asynchronous model requests."""

    def __init__(self) -> None:
        self._tool_rounds = 0
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._turn_start = 1
        self._turn_starts: list[int] = []
        self._slider_crank_turn = False
        self._creation_requested = False
        self._creation_attempted = False
        self._creation_succeeded = False
        self._missing_tool_retry = False
        self._has_image = False
        self.creation_error = ""
        self._last_limited_content: str | None = None
        self._empty_limit_retry = False

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    @property
    def request_messages(self) -> list[dict[str, Any]]:
        prompt = SYSTEM_PROMPT
        if self._slider_crank_turn:
            prompt += "\n\n" + SLIDER_CRANK_PROMPT
        return [{"role": "system", "content": prompt}, *self._messages[1:]]

    @property
    def tool_choice(self) -> str | dict[str, Any]:
        if self._slider_crank_turn and self._tool_rounds == 0:
            return {"type": "function", "function": {"name": "build_slider_crank_from_plan"}}
        if (self._creation_requested and not self._creation_attempted
                and (self._tool_rounds == 0 or self._missing_tool_retry)):
            return "required"
        return "auto"

    def request_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected = self.tool_choice
        if isinstance(selected, dict):
            name = selected["function"]["name"]
            matches = [tool for tool in tools if tool.get("function", {}).get("name") == name]
            if not matches:
                raise ValueError(f"Required modeling tool is unavailable: {name}")
            return matches
        mechanism_context = any(
            call.get("function", {}).get("name") == "build_slider_crank_from_plan"
            for message in self._messages[1:] for call in message.get("tool_calls", []))
        if self._slider_crank_turn or mechanism_context or self._has_image:
            matches = list(tools)
        else:
            matches = [tool for tool in tools if tool.get("function", {}).get("name")
                       != "build_slider_crank_from_plan"]
        if selected == "required" and not any(
                tool.get("function", {}).get("name") in CREATION_TOOLS for tool in matches):
            raise ValueError("No modeling tools are available for this request")
        return matches

    @property
    def creation_requested(self) -> bool:
        return self._creation_requested

    @property
    def creation_attempted(self) -> bool:
        return self._creation_attempted

    @property
    def creation_succeeded(self) -> bool:
        return self._creation_succeeded

    @property
    def creation_summary(self) -> str:
        turn = self._messages[self._turn_start:]
        names = {call.get("id"): call.get("function", {}).get("name")
                 for message in turn for call in message.get("tool_calls", [])}
        lines = []
        for message in turn:
            name = names.get(message.get("tool_call_id"))
            if message.get("role") != "tool" or name not in CREATION_TOOLS:
                continue
            try:
                result = json.loads(message["content"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(result, dict) and result.get("ok") is True:
                target = result.get("label") or result.get("name") or result.get("run_directory") or "success"
                lines.append(f"{name}: {target}")
        return "Modeling tools completed successfully:\n" + "\n".join(lines)

    def retry_missing_tool(self) -> bool:
        if not self._creation_requested or self._creation_attempted or self._missing_tool_retry:
            return False
        self._missing_tool_retry = True
        self._messages.append({"role": "user", "content": (
            "No modeling tool has run and no model has been created. Call an available "
            "modeling tool now with valid arguments for the original request. Use feasible "
            "defaults for unspecified dimensions and disclose them. Do not just promise "
            "to create a model. If the requested shape is unsupported, explain the limitation."
        )})
        return True

    def continue_after_output_limit(self, content: str | None) -> bool:
        progressed = bool(content and content != self._last_limited_content)
        if not progressed:
            if self._empty_limit_retry:
                return False
            self._empty_limit_retry = True
        else:
            self.accept_assistant({"content": content})
            self._last_limited_content = content
            self._empty_limit_retry = False
        self._messages.append({"role": "user", "content": (
            "The last response reached its output token limit, but this task is not finished. "
            "Continue the original task from the successful tool results above. Do not "
            "recreate objects or repeat completed actions. Keep reasoning and prose concise; "
            "prioritize the remaining tool calls and a final answer. Any truncated tool "
            "call was discarded and NOT executed: send a complete valid call if still needed."
        )})
        return True

    def begin(self, user_text: str, document_context: str,
              image: ImageAttachment | None = None) -> None:
        self._close_pending_tool_calls("Previous request ended before this action was executed.")
        self._tool_rounds = 0
        lowered = user_text.lower()
        self._creation_requested = _requests_creation(user_text)
        self._creation_attempted = False
        self._creation_succeeded = False
        self._missing_tool_retry = False
        self._has_image = image is not None
        self.creation_error = ""
        self._last_limited_content = None
        self._empty_limit_retry = False
        self._slider_crank_turn = (
            ("曲柄滑块" in user_text or "slider-crank" in lowered or "slider crank" in lowered)
            and self._creation_requested
            and not any(word in lowered for word in ("重播", "重新播放", "回放", "replay"))
        )
        self._trim_history(keep_latest_image=image is None)
        self._has_image = image is not None or any(
            isinstance(message.get("content"), list) and
            any(part.get("type") == "image_url" for part in message["content"])
            for message in self._messages[1:])
        if any(word in lowered for word in ("重播", "重新播放", "回放", "replay")):
            self._creation_requested = False
        self._turn_start = len(self._messages)
        self._turn_starts.append(self._turn_start)
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
        call = next((call for message in reversed(self._messages[self._turn_start:])
                     for call in message.get("tool_calls", []) if call.get("id") == call_id), None)
        if call is not None and call.get("function", {}).get("name") in CREATION_TOOLS:
            self._creation_attempted = True
            try:
                data = json.loads(result)
            except (TypeError, json.JSONDecodeError):
                data = {}
            succeeded = isinstance(data, dict) and data.get("ok") is True
            self._creation_succeeded |= succeeded
            if not succeeded:
                self.creation_error = str(data.get("error", "Modeling tool failed")) if isinstance(data, dict) else "Invalid modeling result"
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": result}
        )

    def add_viewport_images(self, data_urls: list[str], *, post_action: bool) -> None:
        if not data_urls:
            return
        prompt = POST_ACTION_REVIEW_PROMPT if post_action else VIEWPORT_TOOL_PROMPT
        self._messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *({"type": "image_url", "image_url": {"url": url}}
                  for url in data_urls),
            ],
        })

    def continue_after_tools(self) -> None:
        self._tool_rounds += 1

    def clear(self) -> None:
        self._tool_rounds = 0
        self._slider_crank_turn = False
        self._creation_requested = False
        self._creation_attempted = False
        self._creation_succeeded = False
        self._missing_tool_retry = False
        self._has_image = False
        self.creation_error = ""
        self._last_limited_content = None
        self._empty_limit_retry = False
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._turn_start = 1
        self._turn_starts.clear()

    def _trim_history(self, *, keep_latest_image: bool) -> None:
        latest_image = next((message for message in reversed(self._messages[1:])
                             if isinstance(message.get("content"), list)
                             and any(part.get("type") == "text" and
                                     part.get("text", "").startswith("Current FreeCAD document:")
                                     for part in message["content"])), None)
        for message in self._messages[1:]:
            content = message.get("content")
            if isinstance(content, list) and not (keep_latest_image and message is latest_image):
                text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
                message["content"] = text + "\n[Earlier image omitted; request a new screenshot if needed.]"

        def history_size() -> int:
            size = 0
            for message in self._messages[1:]:
                content = message.get("content")
                if isinstance(content, list):
                    size += sum(len(part.get("text", "")) for part in content if part.get("type") == "text")
                elif isinstance(content, str):
                    size += len(content)
                size += len(json.dumps(message.get("tool_calls", [])))
            return size

        # Evict whole turns, never an assistant call without its tool results.
        while len(self._turn_starts) > 1 and (
                len(self._turn_starts) > HISTORY_TURNS or history_size() > HISTORY_CHARACTERS):
            start = self._turn_starts[1]
            self._messages = [self._messages[0], *self._messages[start:]]
            self._turn_starts = [index - start + 1 for index in self._turn_starts[1:]]

    def _close_pending_tool_calls(self, reason: str) -> None:
        turn = self._messages[self._turn_start:]
        completed = {item.get("tool_call_id") for item in turn if item["role"] == "tool"}
        for item in turn:
            for call in item.get("tool_calls", []):
                if call["id"] not in completed:
                    self.add_tool_result(call["id"], json.dumps({
                        "ok": False, "error": reason,
                    }))

    def cancel_turn(self) -> None:
        """Close unfinished tool calls without claiming they were executed."""
        self._close_pending_tool_calls("Cancelled by user; action not executed.")
        self._messages.append({"role": "assistant", "content": "Conversation stopped by user."})
        self._slider_crank_turn = False
        self._creation_requested = False
