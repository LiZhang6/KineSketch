"""Create four independent FCStd test parts and a checksum-bound interface manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from freecad.KineSketch.utils.mechanisms.contract import (
    GUIDE_ROTATION, IDENTITY, MechanismError, ROLES, number, run_directory, sha256, write_json,
)
from freecad.KineSketch.utils.mechanisms.freecad_native import modules, version

GENERATOR_VERSION = "slider_crank_fixture_v1"


def frame(x: float, y: float, z: float, *, guide: bool = False) -> dict:
    return {"origin_mm": [x, y, z],
            "rotation_xyzw": list(GUIDE_ROTATION if guide else IDENTITY)}


def manifest_template(radius_mm: object, rod_mm: object) -> dict:
    r = number(radius_mm, "radius_mm", positive=True)
    l = number(rod_mm, "rod_mm", positive=True)
    if l <= r:
        raise MechanismError("UNSUPPORTED_GEOMETRY", "rod length must exceed crank radius")
    interfaces = {
        "ground": {"main_axis": frame(0, 0, 0), "guide_axis": frame(0, 0, 0, guide=True)},
        "crank": {"main_axis": frame(0, 0, 0), "crank_pin": frame(r, 0, 0)},
        "connecting_rod": {"rod_big_end": frame(0, 0, 0), "rod_small_end": frame(l, 0, 0)},
        "slider": {"wrist_pin": frame(0, 0, 0), "guide_axis": frame(0, 0, 0, guide=True)},
    }
    return {"schema_version": 1, "mechanism_type": "slider_crank",
            "generator_version": GENERATOR_VERSION, "units": {"length": "mm", "angle": "rad"},
            "parameters": {"crank_radius_mm": r, "rod_length_mm": l, "offset_mm": 0.0},
            "parts": {role: {"file": role + ".FCStd", "object_name": role.title().replace("_", ""),
                             "interfaces": interfaces[role]} for role in ROLES}}


def _shape(Part, App, role: str, r: float, l: float):
    V = App.Vector
    if role == "ground":
        plate = Part.makeBox(l + r + 90, 44, 6, V(-35, -22, -14))
        rail_a = Part.makeBox(l + r + 70, 4, 8, V(-15, 9, -8))
        rail_b = Part.makeBox(l + r + 70, 4, 8, V(-15, -13, -8))
        pivot = Part.makeCylinder(6, 8, V(0, 0, -8))
        return plate.fuse([rail_a, rail_b, pivot]).removeSplitter()
    if role == "crank":
        bar = Part.makeBox(r, 8, 4, V(0, -4, -2))
        return bar.fuse([Part.makeCylinder(6, 4, V(0, 0, -2)),
                         Part.makeCylinder(5, 4, V(r, 0, -2))]).removeSplitter()
    if role == "connecting_rod":
        bar = Part.makeBox(l, 6, 3, V(0, -3, -1.5))
        return bar.fuse([Part.makeCylinder(5, 3, V(0, 0, -1.5)),
                         Part.makeCylinder(5, 3, V(l, 0, -1.5))]).removeSplitter()
    if role == "slider":
        return Part.makeBox(20, 16, 8, V(-10, -8, -4))
    raise ValueError(role)


def generate_fixture(output_dir: str, radius_mm: object = None, rod_mm: object = None) -> dict:
    """Run within FreeCAD's Python. Does not alter any pre-existing document."""
    if radius_mm is None or rod_mm is None:
        spec = json.loads((Path(__file__).with_name("fixture_spec.json")).read_text(encoding="utf-8"))
        radius_mm = spec["parameters"]["crank_radius_mm"] if radius_mm is None else radius_mm
        rod_mm = spec["parameters"]["rod_length_mm"] if rod_mm is None else rod_mm
    manifest = manifest_template(radius_mm, rod_mm)
    output = run_directory(output_dir)
    App, Part, _, _ = modules()
    previous = App.ActiveDocument.Name if App.ActiveDocument else None
    output.mkdir(parents=True, exist_ok=False)
    try:
        for role in ROLES:
            doc = App.newDocument("KineSketchFixture_" + role)
            try:
                obj = doc.addObject("Part::Feature", manifest["parts"][role]["object_name"])
                obj.Shape = _shape(Part, App, role, manifest["parameters"]["crank_radius_mm"],
                                   manifest["parameters"]["rod_length_mm"])
                if obj.Shape.isNull() or not obj.Shape.isValid():
                    raise MechanismError("FIXTURE_GEOMETRY_FAILED", f"Generated {role} shape is invalid", "failed")
                obj.addProperty("App::PropertyString", "KineSketchRole", "KineSketch")
                obj.KineSketchRole = role
                obj.addProperty("App::PropertyString", "KineSketchGeneratorVersion", "KineSketch")
                obj.KineSketchGeneratorVersion = GENERATOR_VERSION
                doc.recompute()
                path = output / manifest["parts"][role]["file"]
                doc.saveAs(str(path))
                manifest["parts"][role]["sha256"] = sha256(path)
            finally:
                if App.getDocument(doc.Name):
                    App.closeDocument(doc.Name)
        manifest["freecad_version"] = version(App)
        write_json(output / "parts_manifest.json", manifest)
        return {"status": "success", "manifest": str(output / "parts_manifest.json"),
                "parts": {role: str(output / manifest["parts"][role]["file"]) for role in ROLES}}
    finally:
        if previous and App.getDocument(previous):
            App.setActiveDocument(previous)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--radius-mm", type=float)
    parser.add_argument("--rod-mm", type=float)
    args = parser.parse_args()
    try:
        response = generate_fixture(args.output_dir, args.radius_mm, args.rod_mm)
    except MechanismError as exc:
        response = {"status": exc.status, "error": {"code": exc.code, "message": str(exc)}}
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
