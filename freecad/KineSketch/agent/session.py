# SPDX-License-Identifier: LGPL-2.1-or-later

"""Conversation state and tool-call orchestration for the KineSketch agent."""

from __future__ import annotations

from typing import Any


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
For a rail-guided, zero-offset slider-crank with a base, crank, connecting rod
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
If the tool rejects a feature plan, correct its reported issue and retry within
the tool-round limit. Use replay_slider_crank for a later replay request. Do not
approximate this mechanism with unrelated standalone primitive tool calls and
do not emit Python code for execution."""


class AgentSession:
    """Maintain a bounded chat history across asynchronous model requests."""

    def __init__(self, max_tool_rounds: int = 6) -> None:
        self.max_tool_rounds = max_tool_rounds
        self._tool_rounds = 0
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._turn_start = 1
        self._slider_crank_turn = False

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    @property
    def request_messages(self) -> list[dict[str, Any]]:
        return [self._messages[0], *self._messages[self._turn_start :]]

    @property
    def tool_choice(self) -> str | dict[str, Any]:
        if self._slider_crank_turn:
            return {"type": "function", "function": {"name": "build_slider_crank_from_plan"}}
        return "auto"

    def begin(self, user_text: str, document_context: str) -> None:
        self._tool_rounds = 0
        lowered = user_text.lower()
        self._slider_crank_turn = (
            ("曲柄滑块" in user_text or "slider-crank" in lowered or "slider crank" in lowered)
            and any(word in lowered for word in
                    ("创建", "生成", "构建", "演示", "装配", "仿真", "build", "create", "simulate"))
            and not any(word in lowered for word in ("重播", "重新播放", "回放", "replay"))
        )
        self._trim_history()
        self._turn_start = len(self._messages)
        content = f"Current FreeCAD document:\n{document_context}\n\nUser request:\n{user_text}"
        self._messages.append({"role": "user", "content": content})

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
        self._slider_crank_turn = False
        self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._turn_start = 1

    def _trim_history(self) -> None:
        if len(self._messages) > 30:
            self._messages = [self._messages[0], *self._messages[-24:]]
