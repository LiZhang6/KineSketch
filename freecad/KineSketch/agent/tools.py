# SPDX-License-Identifier: LGPL-2.1-or-later

"""Allowlisted model tools that operate on the active FreeCAD document."""

from __future__ import annotations

import json
from typing import Any, Callable

import FreeCAD as App


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_box",
            "description": "Create a parametric Part Box in the active document.",
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
            "description": "Create a parametric Part Cylinder in the active document.",
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
            "description": "Move and rotate an existing object by its internal name.",
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
            "description": "Fit all document objects in the active 3D view.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
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
    objects = [
        {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        for obj in document.Objects
    ]
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
        raise ValueError(f"Object not found: {name}")
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