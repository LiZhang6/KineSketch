# SPDX-License-Identifier: LGPL-2.1-or-later

"""Validated, atomic parametric modeling plans for FreeCAD."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

KINDS = {
    "box": {"length", "width", "height"},
    "cylinder": {"radius", "height"},
    "sphere": {"radius"},
    "fuse": {"base", "tool"},
    "cut": {"base", "tool"},
}
PLACEMENT = {"x", "y", "z", "yaw", "pitch", "roll"}


def plan_schema() -> dict[str, Any]:
    variants = []
    for kind, fields in KINDS.items():
        properties: dict[str, Any] = {
            "id": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "enum": [kind]},
            "label": {"type": "string"},
        }
        for field in sorted(fields):
            properties[field] = (
                {"type": "string"} if kind in {"fuse", "cut"}
                else {"type": "number", "exclusiveMinimum": 0}
            )
        if kind not in {"fuse", "cut"}:
            properties.update({field: {"type": "number"} for field in sorted(PLACEMENT)})
        variants.append({
            "type": "object",
            "properties": properties,
            "required": ["id", "kind", *sorted(fields)],
            "additionalProperties": False,
        })
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "operations": {
                "type": "array", "minItems": 1, "maxItems": 64,
                "items": {"oneOf": variants},
            },
        },
        "required": ["operations"],
        "additionalProperties": False,
    }


def validate_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan, dict) or set(plan) - {"label", "operations"}:
        raise ValueError("Plan must contain only label and operations")
    if "label" in plan and not isinstance(plan["label"], str):
        raise ValueError("Plan label must be a string")
    operations = plan.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 64:
        raise ValueError("Plan requires 1 to 64 operations")
    seen: set[str] = set()
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError("Each operation must be an object")
        kind = op.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError(f"Unsupported operation kind: {kind}")
        identifier = op.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError("Operation IDs must be non-empty unique strings")
        required = KINDS[kind]
        allowed = {"id", "kind", "label"} | required
        if kind not in {"fuse", "cut"}:
            allowed |= PLACEMENT
        if set(op) - allowed or not required <= set(op):
            raise ValueError(f"Invalid or missing fields for {identifier}")
        if "label" in op and not isinstance(op["label"], str):
            raise ValueError("Operation label must be a string")
        if kind in {"fuse", "cut"}:
            for key in required:
                if not isinstance(op[key], str) or op[key] not in seen:
                    raise ValueError(f"{key} must reference an earlier operation")
            if op["base"] == op["tool"]:
                raise ValueError("Boolean operands must be different")
        else:
            for key in (required | PLACEMENT) & set(op):
                value = op[key]
                if isinstance(value, bool) or not isinstance(value, (float, int)):
                    raise ValueError(f"{key} must be a finite number")
                if not math.isfinite(value) or (key in required and value <= 0):
                    raise ValueError(f"Invalid dimension or placement: {key}")
        seen.add(identifier)
    return operations


def generate_model(plan: dict[str, Any]) -> dict[str, Any]:
    operations = validate_plan(plan)
    import FreeCAD as App

    existing = App.ActiveDocument
    document = existing or App.newDocument("KineSketch")
    objects: dict[str, Any] = {}
    consumed: set[str] = set()
    document.openTransaction("KineSketch: Generate 3D model")
    try:
        for op in operations:
            kind = op["kind"]
            type_name = kind.capitalize()
            obj = document.addObject(f"Part::{type_name}", "Generated" + type_name)
            obj.Label = op.get("label", op["id"])
            if kind in {"cut", "fuse"}:
                obj.Base = objects[op["base"]]
                obj.Tool = objects[op["tool"]]
                consumed.update((op["base"], op["tool"]))
            else:
                for field in KINDS[kind]:
                    setattr(obj, field.capitalize(), float(op[field]))
                obj.Placement = App.Placement(
                    App.Vector(*(op.get(axis, 0) for axis in ("x", "y", "z"))),
                    App.Rotation(*(op.get(axis, 0) for axis in ("yaw", "pitch", "roll"))),
                )
            objects[op["id"]] = obj
            document.recompute()
            if obj.Shape.isNull() or not obj.Shape.isValid() or obj.Shape.Volume <= 0:
                raise ValueError(f"Operation {op['id']} did not produce a valid solid")
        roots = [obj for key, obj in objects.items() if key not in consumed]
        if plan.get("label") and len(roots) == 1:
            roots[0].Label = plan["label"]
        if App.GuiUp:
            for key, obj in objects.items():
                obj.ViewObject.Visibility = key not in consumed
            view = App.Gui.activeDocument().activeView()
            view.viewAxonometric()
            view.fitAll()
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        if existing is None:
            App.closeDocument(document.Name)
        raise
    return {
        "ok": True,
        "document": document.Name,
        "objects": {key: obj.Name for key, obj in objects.items()},
        "results": [obj.Name for obj in roots],
    }


def save_model(path: str) -> dict[str, Any]:
    import FreeCAD as App

    destination = Path(path)
    if not destination.is_absolute():
        raise ValueError("Output path must be absolute")
    if not destination.suffix:
        destination = destination.with_suffix(".FCStd")
    if destination.exists():
        raise ValueError("Output file already exists; choose a new path")
    suffix = destination.suffix.lower()
    if suffix not in {".fcstd", ".step", ".stp", ".stl"}:
        raise ValueError("Supported formats: FCStd, STEP, STL")
    document = App.ActiveDocument
    if document is None:
        raise ValueError("No active document")
    document.recompute()
    if suffix == ".fcstd":
        document.saveAs(str(destination))
    else:
        objects = [
            obj for obj in document.Objects
            if hasattr(obj, "Shape") and not obj.Shape.isNull() and not obj.InList
        ]
        if not objects:
            raise ValueError("No result solids to export")
        if suffix == ".stl":
            import Mesh

            Mesh.export(objects, str(destination))
        else:
            import Part

            Part.export(objects, str(destination))
    return {
        "ok": True,
        "path": str(destination),
        "document": document.Name,
        "format": {".fcstd": "FCStd", ".step": "STEP", ".stp": "STEP", ".stl": "STL"}[suffix],
        "parametric": suffix == ".fcstd",
    }
