# SPDX-License-Identifier: LGPL-2.1-or-later

"""Allowlisted model tools that operate on the active FreeCAD document."""

from __future__ import annotations

import json
from typing import Any, Callable

import FreeCAD as App


_FEATURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "An additive FreeCAD solid in local millimetre coordinates. A box uses "
        "origin_mm as its minimum XYZ corner and size_mm as XYZ lengths. A cylinder "
        "uses origin_mm as the centre of its bottom face, points along +Z, and uses "
        "radius_mm and height_mm. Supply only the fields for its kind."
    ),
    "properties": {
        "kind": {"type": "string", "enum": ["box", "cylinder"]},
        "origin_mm": {"type": "array", "items": {"type": "number"},
                      "minItems": 3, "maxItems": 3},
        "size_mm": {"type": "array", "items": {"type": "number"},
                    "minItems": 3, "maxItems": 3},
        "radius_mm": {"type": "number"},
        "height_mm": {"type": "number"},
    },
    "required": ["kind", "origin_mm"],
    "additionalProperties": False,
}

_PART_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "features": {"type": "array", "items": _FEATURE_SCHEMA,
                     "minItems": 1, "maxItems": 12},
    },
    "required": ["label", "features"],
    "additionalProperties": False,
}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_box",
            "description": (
                "Create a parametric Part Box in the active document. "
                "x/y/z specify its minimum XYZ corner, NOT the centre of its bottom face. "
                "Length, width and height extend along +X, +Y and +Z in millimetres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "length": {"type": "number", "exclusiveMinimum": 0},
                    "width": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                    "x": {"type": "number", "default": 0},
                    "y": {"type": "number", "default": 0},
                    "z": {"type": "number", "default": 0},
                },
                "required": ["length", "width", "height"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_cylinder",
            "description": (
                "Create a parametric Part Cylinder in the active document. "
                "x/y/z specify the centre of its bottom face; its axis points along +Z. "
                "All dimensions and coordinates are in millimetres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                    "x": {"type": "number", "default": 0},
                    "y": {"type": "number", "default": 0},
                    "z": {"type": "number", "default": 0},
                },
                "required": ["radius", "height"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_placement",
            "description": (
                "Set the absolute position and rotation of an existing object. Prefer the "
                "internal name returned by create_box/create_cylinder; a unique exact label "
                "is also accepted. Coordinates are in millimetres. "
                "yaw is rotation about Z, pitch about Y, roll about X, in degrees."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "x": {"type": "number", "default": 0},
                    "y": {"type": "number", "default": 0},
                    "z": {"type": "number", "default": 0},
                    "yaw": {"type": "number", "default": 0},
                    "pitch": {"type": "number", "default": 0},
                    "roll": {"type": "number", "default": 0},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fit_view",
            "description": "Switch to axonometric view and fit all objects in the active 3D view.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_viewport",
            "description": (
                "Capture the current FreeCAD 3D viewport. The PNG will be sent as "
                "a real image in the next model request, so call this when you "
                "need to visually inspect the result. The tool result itself "
                "contains only image metadata. A screenshot cannot prove hidden "
                "geometry, exact dimensions, or solver correctness."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_slider_crank_from_plan",
            "description": (
                "Build a zero-offset, rail-guided slider-crank from YOUR explicit "
                "box/cylinder feature plan. The tool does not load or invoke the "
                "prebuilt demo fixture: it generates four fresh FCStd solids from "
                "the supplied plan, then applies the Assembly and Kinematic skills "
                "to verify joints, simulate motion, export viewport MP4, and play "
                "in the FreeCAD GUI. Supported topology: ground/base with two rails "
                "and pivot, crank, connecting rod, slider. Local crank endpoints are "
                "(0,0,0) and (crank_radius_mm,0,0); rod endpoints are (0,0,0) and "
                "(rod_length_mm,0,0); ground pivot and slider pin are at (0,0,0). "
                "Every part must be one connected solid with material at those "
                "joint points. Ground needs >=3 boxes and >=1 cylinder, crank and "
                "rod each >=1 box and >=2 cylinders, slider >=1 box. Make a base "
                "long enough for slider travel and give the slider clearance "
                "between the rails. Box origins are MINIMUM corners, not centres. "
                "Ground plate lies below z=0 and spans +/-Y; pivot cylinder "
                "centres on x=0,y=0. Rails lie on the plate at opposite +/-Y. "
                "Centre moving bars and slider body about local y=0; centre "
                "the slider body on x=0. Each feature must overlap its part's "
                "other features into one connected solid. This is not a general "
                "mechanism solver."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "crank_radius_mm": {"type": "number"},
                    "rod_length_mm": {"type": "number"},
                    "initial_angle_deg": {"type": "number", "default": 30},
                    "rpm": {"type": "number", "default": 60},
                    "cycles": {"type": "number", "default": 2},
                    "export_video": {"type": "boolean", "default": True},
                    "playback_seconds": {"type": "number", "default": 60},
                    "parts": {
                        "type": "object",
                        "properties": {role: _PART_SCHEMA for role in
                                       ("ground", "crank", "connecting_rod", "slider")},
                        "required": ["ground", "crank", "connecting_rod", "slider"],
                        "additionalProperties": False,
                    },
                },
                "required": ["crank_radius_mm", "rod_length_mm", "parts"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replay_slider_crank",
            "description": "Replay the last verified slider-crank simulation in the FreeCAD GUI for a specified duration.",
            "parameters": {
                "type": "object",
                "properties": {"playback_seconds": {"type": "number", "default": 60}},
                "additionalProperties": False,
            },
        },
    },
]


def document_summary() -> str:
    """Return compact document and selection context suitable for a model prompt."""
    document = App.ActiveDocument
    if document is None:
        return "No active document. A new document will be created when geometry is added."
    selected_names: list[str] = []
    if App.GuiUp:
        selected_names = [obj.Name for obj in App.Gui.Selection.getSelection()]
    objects = []
    for obj in document.Objects:
        item = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        properties = getattr(obj, "PropertiesList", ())
        dimensions = {}
        for name in ("Length", "Width", "Height", "Radius"):
            if name in properties:
                value = getattr(obj, name)
                dimensions[name.lower()] = float(getattr(value, "Value", value))
        if dimensions:
            item["dimensions_mm"] = dimensions
        if "Placement" in properties:
            placement = obj.Placement
            item["position_mm"] = [placement.Base.x, placement.Base.y, placement.Base.z]
            item["rotation_quaternion_xyzw"] = list(placement.Rotation.Q)
        objects.append(item)
    return json.dumps(
        {"name": document.Name, "objects": objects, "selected": selected_names},
        ensure_ascii=False,
    )


def execute_tool_call(tool_call: dict[str, Any]) -> str:
    """Validate and execute one OpenAI-style function tool call."""
    try:
        function = tool_call["function"]
        name = function["name"]
        arguments = json.loads(function.get("arguments") or "{}")
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        return json.dumps({"ok": False, "error": f"Invalid tool call: {error}"})
    if not isinstance(arguments, dict):
        return json.dumps({"ok": False, "error": "Tool arguments must be an object"})

    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "create_box": _create_box,
        "create_cylinder": _create_cylinder,
        "set_placement": _set_placement,
        "fit_view": _fit_view,
        "capture_viewport": _capture_viewport,
        "build_slider_crank_from_plan": _build_slider_crank_from_plan,
        "create_slider_crank_demo": _create_slider_crank_demo,
        "replay_slider_crank": _replay_slider_crank,
    }
    handler = handlers.get(name)
    if handler is None:
        return json.dumps({"ok": False, "error": f"Unknown tool: {name}"})
    try:
        return json.dumps(handler(arguments), ensure_ascii=False)
    except (KeyError, TypeError, ValueError) as error:
        return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
    except Exception as error:
        App.Console.PrintError(f"KineSketch agent tool failed: {error}\n")
        return json.dumps({"ok": False, "error": f"FreeCAD operation failed: {error}"})


def local_tool_summary(name: str, result: str) -> str | None:
    """Give long-running demo tools a local final report without another SSH round trip."""
    if name not in ("build_slider_crank_from_plan", "create_slider_crank_demo",
                    "replay_slider_crank"):
        return None
    try:
        data = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("ok"):
        stage = data.get("stage", "tool")
        error = data.get("error", "Unknown error")
        directory = data.get("run_directory")
        return f"曲柄滑块演示在 {stage} 阶段失败：{error}" + (
            f"\n诊断目录：{directory}" if directory else ""
        )
    if name == "replay_slider_crank":
        return f"已在 FreeCAD GUI 中重新播放曲柄滑块运动，持续 {data['playback_seconds']:g} 秒。"
    geometry = data["geometry"]
    assembly = data["assembly"]
    simulation = data["simulation"]
    lines = [
        ("依据文字规划的曲柄滑块机构已完成："
         if name == "build_slider_crank_from_plan" else "曲柄滑块机构完整演示已完成："),
        f"1. 零件：已生成底座、曲柄、连杆和滑块四个独立 3D 零件；预览：{geometry['preview']}",
        f"2. 装配：FreeCAD 原生 Assembly 求解返回 {assembly['solver_return_code']}；文件：{assembly['project']}",
        f"3. 仿真：{simulation['sampled_frames']} 个采样帧、{simulation['native_frames']} 个原生帧；"
        f"文件：{simulation['project']}；数据：{simulation['csv']}",
    ]
    if "video" in data:
        lines.append(
            f"4. 视频：{data['video']['verified_frames']} 帧 FreeCAD 视口 MP4；"
            f"文件：{data['video']['mp4']}"
        )
    if "design_plan" in geometry:
        lines.append(f"模型给出的几何特征方案：{geometry['design_plan']}")
    lines.append(f"GUI 运动回放已启动，持续 {data['playback_seconds']:g} 秒。")
    return "\n".join(lines)


def _active_document():
    return App.ActiveDocument or App.newDocument("KineSketch")


def _number(arguments: dict[str, Any], name: str, default: float | None = None) -> float:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    return float(value)


def _positive(arguments: dict[str, Any], name: str) -> float:
    value = _number(arguments, name)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _label(arguments: dict[str, Any], fallback: str) -> str:
    value = arguments.get("label", fallback)
    if not isinstance(value, str):
        raise TypeError("label must be a string")
    return value.strip()[:120] or fallback


def _position(arguments: dict[str, Any]):
    return App.Vector(
        _number(arguments, "x", 0),
        _number(arguments, "y", 0),
        _number(arguments, "z", 0),
    )


def _create_box(arguments: dict[str, Any]) -> dict[str, Any]:
    document = _active_document()
    document.openTransaction("KineSketch Agent: Create box")
    try:
        obj = document.addObject("Part::Box", "AgentBox")
        obj.Label = _label(arguments, "Agent Box")
        obj.Length = _positive(arguments, "length")
        obj.Width = _positive(arguments, "width")
        obj.Height = _positive(arguments, "height")
        obj.Placement.Base = _position(arguments)
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return {"ok": True, "name": obj.Name, "label": obj.Label}


def _create_cylinder(arguments: dict[str, Any]) -> dict[str, Any]:
    document = _active_document()
    document.openTransaction("KineSketch Agent: Create cylinder")
    try:
        obj = document.addObject("Part::Cylinder", "AgentCylinder")
        obj.Label = _label(arguments, "Agent Cylinder")
        obj.Radius = _positive(arguments, "radius")
        obj.Height = _positive(arguments, "height")
        obj.Placement.Base = _position(arguments)
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return {"ok": True, "name": obj.Name, "label": obj.Label}


def _set_placement(arguments: dict[str, Any]) -> dict[str, Any]:
    name = arguments["name"]
    if not isinstance(name, str) or not name:
        raise TypeError("name must be a non-empty string")
    document = App.ActiveDocument
    if document is None:
        raise ValueError("No active document")
    obj = document.getObject(name)
    if obj is None:
        matches = document.getObjectsByLabel(name)
        if len(matches) > 1:
            names = ", ".join(match.Name for match in matches)
            raise ValueError(f"Ambiguous object label: {name}. Use an internal name: {names}")
        if not matches:
            raise ValueError(f"Object not found: {name}")
        obj = matches[0]
    document.openTransaction("KineSketch Agent: Set placement")
    try:
        obj.Placement = App.Placement(
            _position(arguments),
            App.Rotation(
                _number(arguments, "yaw", 0),
                _number(arguments, "pitch", 0),
                _number(arguments, "roll", 0),
            ),
        )
        document.recompute()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return {"ok": True, "name": obj.Name, "label": obj.Label}


def _fit_view(arguments: dict[str, Any]) -> dict[str, Any]:
    del arguments
    if not App.GuiUp or App.ActiveDocument is None:
        raise ValueError("No active 3D view")
    App.Gui.activeDocument().activeView().viewAxonometric()
    App.Gui.activeDocument().activeView().fitAll()
    return {"ok": True}


def _capture_viewport(arguments: dict[str, Any]) -> dict[str, Any]:
    from .vision import capture_viewport

    return capture_viewport(arguments)


def _create_slider_crank_demo(arguments: dict[str, Any]) -> dict[str, Any]:
    from .slider_crank_demo import create_slider_crank_demo

    return create_slider_crank_demo(arguments)


def _build_slider_crank_from_plan(arguments: dict[str, Any]) -> dict[str, Any]:
    from .slider_crank_demo import create_slider_crank_from_plan

    return create_slider_crank_from_plan(arguments)


def _replay_slider_crank(arguments: dict[str, Any]) -> dict[str, Any]:
    from .slider_crank_demo import replay_slider_crank

    seconds = _number(arguments, "playback_seconds", 60)
    if not 5 <= seconds <= 300:
        raise ValueError("playback_seconds must be between 5 and 300")
    return replay_slider_crank(seconds)
