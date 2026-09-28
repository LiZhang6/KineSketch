# SPDX-License-Identifier: LGPL-2.1-or-later

"""Parametric crank arm with mass properties derived from a uniform solid."""

from __future__ import annotations

import math
from typing import Any

DIMENSIONS = {
    "center_distance": "CrankRadius",
    "arm_width": "ArmWidth",
    "thickness": "Thickness",
    "shaft_diameter": "ShaftDiameter",
    "pin_diameter": "PinDiameter",
}
INERTIA_FIELDS = {"Ixx": "A11", "Iyy": "A22", "Izz": "A33",
                  "Ixy": "A12", "Ixz": "A13", "Iyz": "A23"}


def crank_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            **{key: {"type": "number", "exclusiveMinimum": 0} for key in DIMENSIONS},
            "density": {"type": "number", "exclusiveMinimum": 0,
                        "description": "Uniform density in kg/m^3; omitted uses an unverified 7850 prototype assumption."},
            "density_source": {"type": "string", "minLength": 1,
                               "description": "Measurement, datasheet or stated assumption."},
            "material": {"type": "string"},
            "label": {"type": "string"},
        },
        "required": [],
        "additionalProperties": False,
    }


def prepare_crank(arguments: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fill missing prototype inputs without overwriting explicit constraints."""
    resolved = dict(arguments)
    assumptions: list[str] = []
    allowed = {*DIMENSIONS, "density", "density_source", "material", "label"}
    if set(resolved) - allowed:
        raise ValueError("Unknown crank parameters")
    for key in ("density_source", "material", "label"):
        if key in resolved and (not isinstance(resolved[key], str)
                                or (key == "density_source" and not resolved[key].strip())):
            raise ValueError(f"{key} must be a non-empty string" if key == "density_source"
                             else f"{key} must be a string")
    for key in (*DIMENSIONS, "density"):
        if key in resolved:
            value = resolved[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} must be a positive finite number")

    def fill(key: str, value: float, unit: str) -> None:
        if key not in resolved:
            resolved[key] = value
            assumptions.append(f"{key}={value:.17g} {unit} (prototype default)")

    largest_hole = max(resolved.get("shaft_diameter", 0), resolved.get("pin_diameter", 0))
    fill("center_distance", max(100.0, 2 * largest_hole), "mm")
    length = resolved["center_distance"]
    fill("arm_width", max(0.3 * length, 1.5 * largest_hole), "mm")
    fill("thickness", 0.08 * length, "mm")
    for key, other, base in (("shaft_diameter", "pin_diameter", 12.0),
                             ("pin_diameter", "shaft_diameter", 8.0)):
        # Leave clearance around holes and between centres even for partial inputs.
        fill(key, min(base * length / 100, 0.4 * resolved["arm_width"],
                      0.4 * length, (2 * length - resolved.get(other, 0)) / 2), "mm")
    if "density" not in resolved:
        fill("density", 7850.0, "kg/m^3")
        source = "Prototype density assumption: 7850 kg/m^3; not verified material data"
        if resolved.get("density_source"):
            source += f"; supplied source without a density value: {resolved['density_source']}"
        resolved["density_source"] = source
    elif "density_source" not in resolved:
        resolved["density_source"] = "User-supplied density; source unspecified and unverified"
        assumptions.append(resolved["density_source"])
    validate_crank(resolved)
    return resolved, assumptions


def validate_crank(arguments: dict[str, Any]) -> None:
    allowed = {*DIMENSIONS, "density", "density_source", "material", "label"}
    if set(arguments) - allowed:
        raise ValueError("Unknown crank parameters")
    for key in [*DIMENSIONS, "density"]:
        value = arguments.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError(f"{key} must be a positive finite number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive finite number")
    for key in ("density_source", "material", "label"):
        if key in arguments and not isinstance(arguments[key], str):
            raise ValueError(f"{key} must be a string")
    if not arguments.get("density_source", "").strip():
        raise ValueError("Density source is required; do not infer density from appearance")
    diameter_max = max(arguments["shaft_diameter"], arguments["pin_diameter"])
    if diameter_max >= arguments["arm_width"]:
        raise ValueError("Hole diameters must be smaller than arm width")
    hole_radii_sum = (arguments["shaft_diameter"] + arguments["pin_diameter"]) / 2
    if hole_radii_sum >= arguments["center_distance"]:
        raise ValueError("Shaft and pin holes must not overlap or touch")


def mass_properties(shape: Any, density: float) -> dict[str, Any]:
    """Convert OCC volume integrals in mm to mass and centroidal inertia in SI."""
    volume = float(shape.Volume) * 1e-9
    mass = density * volume
    centre = shape.CenterOfMass
    matrix = shape.MatrixOfInertia
    inertia = {key: float(getattr(matrix, field)) * density * 1e-15
               for key, field in INERTIA_FIELDS.items()}
    # Parallel-axis theorem about the shaft's local Z axis through x=y=0.
    shaft_inertia = inertia["Izz"] + mass * ((centre.x * 1e-3) ** 2 + (centre.y * 1e-3) ** 2)
    return {
        "volume_m3": volume,
        "mass_kg": mass,
        "center_of_mass_mm": [centre.x, centre.y, centre.z],
        "inertia_kg_m2": inertia,
        "shaft_inertia_kg_m2": shaft_inertia,
        "frame": "Body-local XYZ; tensor about centre of mass; shaft along local Z",
    }


class CrankProxy:
    """Rebuild geometry and physical properties whenever input properties change."""

    def __init__(self) -> None:
        self.properties = None

    def execute(self, obj) -> None:
        import FreeCAD as App
        import Part

        self.error = ""
        self.properties = None
        try:
            arguments = {key: float(getattr(obj, prop).Value) for key, prop in DIMENSIONS.items()}
            arguments["density"] = float(obj.Density.getValueAs("kg/m^3").Value)
            arguments["density_source"] = obj.DensitySource
            validate_crank(arguments)
            length, width, height = (arguments[key] for key in
                                     ("center_distance", "arm_width", "thickness"))
            body = Part.makeBox(length, width, height, App.Vector(0, -width / 2, 0))
            for x in (0, length):
                body = body.fuse(Part.makeCylinder(width / 2, height, App.Vector(x, 0, 0)))
            margin = max(1.0, height * 0.01)
            for x, diameter in ((0, arguments["shaft_diameter"]),
                                (length, arguments["pin_diameter"])):
                body = body.cut(Part.makeCylinder(diameter / 2, height + 2 * margin,
                                                 App.Vector(x, 0, -margin)))
            body = body.removeSplitter()
            if body.isNull() or not body.isValid() or len(body.Solids) != 1 or body.Volume <= 0:
                raise ValueError("Crank must be one valid solid")
            properties = mass_properties(body, arguments["density"])
            obj.Shape = body
            obj.Mass = f"{properties['mass_kg']:.17g} kg"
            obj.CenterOfMass = body.CenterOfMass
            for key, value in properties["inertia_kg_m2"].items():
                setattr(obj, key, f"{value:.17g} kg*m^2")
            obj.ShaftInertia = f"{properties['shaft_inertia_kg_m2']:.17g} kg*m^2"
            self.properties = properties
        except Exception as error:
            self.error = str(error)
            raise

    def dumps(self):
        return None

    def loads(self, state):
        self.error = ""
        self.properties = None


def create_crank(arguments: dict[str, Any]) -> dict[str, Any]:
    arguments, assumptions = prepare_crank(arguments)
    import FreeCAD as App

    existing = App.ActiveDocument
    document = existing or App.newDocument("KineSketch")
    document.openTransaction("KineSketch: Create physical crank")
    try:
        obj = document.addObject("Part::FeaturePython", "Crank")
        obj.Label = arguments.get("label", "Crank")
        for key, name in DIMENSIONS.items():
            obj.addProperty("App::PropertyLength", name, "Crank geometry")
            setattr(obj, name, float(arguments[key]))
        obj.addProperty("App::PropertyDensity", "Density", "Physics")
        obj.addProperty("App::PropertyMass", "Mass", "Physics")
        for name in (*INERTIA_FIELDS, "ShaftInertia"):
            obj.addProperty("App::PropertyQuantity", name, "Physics")
            setattr(obj, name, App.Units.Unit("kg*m^2"))
        for name in ("Material", "DensitySource", "PhysicsAssumption", "InertiaFrame"):
            obj.addProperty("App::PropertyString", name, "Physics")
        obj.addProperty("App::PropertyVector", "CenterOfMass", "Physics")
        obj.addProperty("App::PropertyStringList", "ModelingAssumptions", "Modeling")
        obj.ModelingAssumptions = assumptions
        obj.Material = arguments.get("material", "Unspecified")
        obj.DensitySource = arguments["density_source"]
        obj.PhysicsAssumption = "Uniform-density rigid solid; no bearings, shaft or pin mass"
        obj.InertiaFrame = "Body-local XYZ, centre of mass; shaft is local Z at x=y=0"
        obj.Density = f"{arguments['density']:.17g} kg/m^3"
        obj.Proxy = CrankProxy()
        if App.GuiUp:
            obj.ViewObject.Proxy = 0
        document.recompute()
        if getattr(obj.Proxy, "error", "") or obj.Shape.isNull():
            raise ValueError(getattr(obj.Proxy, "error", "") or "Crank recompute failed")
        for name in ("Mass", "CenterOfMass", *INERTIA_FIELDS, "ShaftInertia",
                     "PhysicsAssumption", "InertiaFrame"):
            obj.setEditorMode(name, 1)
        properties = obj.Proxy.properties
        if properties is None:
            properties = mass_properties(obj.Shape, arguments["density"])
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        if existing is None:
            App.closeDocument(document.Name)
        raise
    return {"ok": True, "name": obj.Name, "document": document.Name,
            "density_kg_m3": arguments["density"],
            "density_source": arguments["density_source"],
            "parameters": arguments, "modeling_assumptions": assumptions,
            "assumption": obj.PhysicsAssumption, **properties}
