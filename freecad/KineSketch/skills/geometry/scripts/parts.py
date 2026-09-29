"""Build four fresh slider-crank solids from an explicit feature plan."""

from __future__ import annotations

import math
from pathlib import Path

from freecad.KineSketch.utils.mechanisms.contract import (
    GUIDE_ROTATION, IDENTITY, MechanismError, ROLES, number, run_directory, sha256,
    write_json,
)


GENERATOR_VERSION = "model_authored_features_v1"
_REQUIRED_KINDS = {
    "ground": {"box": 3, "cylinder": 1},
    "crank": {"box": 1, "cylinder": 2},
    "connecting_rod": {"box": 1, "cylinder": 2},
    "slider": {"box": 1},
}


def _bounded(value: object, field: str, minimum: float, maximum: float) -> float:
    result = number(value, field)
    if not minimum <= result <= maximum:
        raise MechanismError("PLAN_DIMENSION", f"{field} must be from {minimum} to {maximum} mm")
    return result


def _vector(value: object, field: str, minimum: float, maximum: float) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise MechanismError("PLAN_VECTOR", f"{field} must contain three millimetre values")
    return [_bounded(component, f"{field}[{index}]", minimum, maximum)
            for index, component in enumerate(value)]


def validate_parts_plan(parts_plan: object) -> dict:
    """Return a canonical, bounded plan before creating any FreeCAD document."""
    if not isinstance(parts_plan, dict) or set(parts_plan) != set(ROLES):
        raise MechanismError("PLAN_ROLES", f"parts must contain exactly {', '.join(ROLES)}")
    normalized = {}
    for role in ROLES:
        part = parts_plan[role]
        if not isinstance(part, dict) or set(part) != {"label", "features"}:
            raise MechanismError("PLAN_PART", f"{role} needs label and features")
        label = part["label"]
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise MechanismError("PLAN_LABEL", f"{role}.label must be 1 to 80 characters")
        features = part["features"]
        if not isinstance(features, list) or not 1 <= len(features) <= 12:
            raise MechanismError("PLAN_FEATURES", f"{role}.features must contain 1 to 12 features")
        counts = {"box": 0, "cylinder": 0}
        validated = []
        for index, feature in enumerate(features):
            field = f"{role}.features[{index}]"
            if not isinstance(feature, dict):
                raise MechanismError("PLAN_FEATURE", f"{field} must be an object")
            kind = feature.get("kind")
            if kind not in counts:
                raise MechanismError("PLAN_FEATURE", f"{field}.kind must be box or cylinder")
            origin = _vector(feature.get("origin_mm"), f"{field}.origin_mm", -5000, 5000)
            if kind == "box":
                if set(feature) != {"kind", "origin_mm", "size_mm"}:
                    raise MechanismError("PLAN_FEATURE", f"{field} needs only kind, origin_mm and size_mm")
                size = _vector(feature["size_mm"], f"{field}.size_mm", 0.1, 5000)
                validated.append({"kind": "box", "origin_mm": origin, "size_mm": size})
            else:
                if set(feature) != {"kind", "origin_mm", "radius_mm", "height_mm"}:
                    raise MechanismError("PLAN_FEATURE", f"{field} needs only kind, origin_mm, radius_mm and height_mm")
                radius = _bounded(feature["radius_mm"], f"{field}.radius_mm", 0.1, 500)
                height = _bounded(feature["height_mm"], f"{field}.height_mm", 0.1, 2000)
                validated.append({"kind": "cylinder", "origin_mm": origin,
                                  "radius_mm": radius, "height_mm": height})
            counts[kind] += 1
        for kind, minimum in _REQUIRED_KINDS[role].items():
            if counts[kind] < minimum:
                raise MechanismError("PLAN_TOPOLOGY", f"{role} needs at least {minimum} {kind} features")
        normalized[role] = {"label": label.strip(), "features": validated}
    return normalized


def _frame(x: float, *, guide: bool = False) -> dict:
    return {"origin_mm": [x, 0.0, 0.0],
            "rotation_xyzw": list(GUIDE_ROTATION if guide else IDENTITY)}


def _manifest(radius: float, rod: float) -> dict:
    interfaces = {
        "ground": {"main_axis": _frame(0), "guide_axis": _frame(0, guide=True)},
        "crank": {"main_axis": _frame(0), "crank_pin": _frame(radius)},
        "connecting_rod": {"rod_big_end": _frame(0), "rod_small_end": _frame(rod)},
        "slider": {"wrist_pin": _frame(0), "guide_axis": _frame(0, guide=True)},
    }
    return {"schema_version": 1, "mechanism_type": "slider_crank",
            "generator_version": GENERATOR_VERSION,
            "units": {"length": "mm", "angle": "rad"},
            "parameters": {"crank_radius_mm": radius, "rod_length_mm": rod,
                           "offset_mm": 0.0},
            "parts": {role: {"file": role + ".FCStd",
                             "object_name": role.title().replace("_", ""),
                             "interfaces": interfaces[role]} for role in ROLES}}


def generate_parts_from_plan(output_dir: str, radius_mm: object,
                             rod_mm: object, parts_plan: object) -> dict:
    """Make new FCStd parts; no fixture solids or existing part files are used."""
    radius = _bounded(radius_mm, "crank_radius_mm", 1, 500)
    rod = _bounded(rod_mm, "rod_length_mm", 2, 2000)
    if rod <= radius:
        raise MechanismError("PLAN_DIMENSION", "rod_length_mm must exceed crank_radius_mm")
    plan = validate_parts_plan(parts_plan)
    output = run_directory(output_dir)

    import FreeCAD as App
    import Part

    previous = App.ActiveDocument.Name if App.ActiveDocument else None
    manifest = _manifest(radius, rod)
    output.mkdir(parents=True, exist_ok=False)
    try:
        write_json(output / "design_plan.json", {
            "generator_version": GENERATOR_VERSION,
            "parameters": dict(manifest["parameters"]), "parts": plan,
        })
        for role in ROLES:
            doc = App.newDocument("KineSketchPlan_" + role)
            try:
                shape = None
                for feature in plan[role]["features"]:
                    base = App.Vector(*feature["origin_mm"])
                    if feature["kind"] == "box":
                        solid = Part.makeBox(*feature["size_mm"], base)
                    else:
                        solid = Part.makeCylinder(feature["radius_mm"],
                                                  feature["height_mm"], base)
                    shape = solid if shape is None else shape.fuse(solid)
                shape = shape.removeSplitter()
                if (shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
                        or not math.isfinite(shape.Volume) or shape.Volume <= 0):
                    raise MechanismError("PLAN_SOLID", f"{role} is not one valid solid", "failed")
                for interface_name, frame in manifest["parts"][role]["interfaces"].items():
                    point = App.Vector(*frame["origin_mm"])
                    if not shape.isInside(point, 1e-6, True):
                        raise MechanismError("PLAN_INTERFACE",
                                             f"{role}.{interface_name} has no material at its joint origin", "failed")
                obj = doc.addObject("Part::Feature", manifest["parts"][role]["object_name"])
                obj.Label = plan[role]["label"]
                obj.Shape = shape
                obj.addProperty("App::PropertyString", "KineSketchSource", "KineSketch")
                obj.KineSketchSource = GENERATOR_VERSION
                doc.recompute()
                path = output / manifest["parts"][role]["file"]
                doc.saveAs(str(path))
                manifest["parts"][role]["sha256"] = sha256(path)
            finally:
                if App.getDocument(doc.Name):
                    App.closeDocument(doc.Name)
        write_json(output / "parts_manifest.json", manifest)
        return {"status": "success", "manifest": str(output / "parts_manifest.json"),
                "design_plan": str(output / "design_plan.json"),
                "parts": {role: str(output / manifest["parts"][role]["file"])
                          for role in ROLES}}
    finally:
        if previous and App.getDocument(previous):
            App.setActiveDocument(previous)
