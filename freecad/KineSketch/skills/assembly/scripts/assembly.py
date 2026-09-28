"""Build and validate a native FreeCAD slider-crank assembly."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from freecad.KineSketch.utils.mechanisms.contract import (
    JOINTS, ROLES, MechanismError, number, read_json, result, run_directory,
    sha256, validate_manifest, write_json,
)
from freecad.KineSketch.utils.mechanisms.freecad_native import (
    assert_residuals, check_methods, create_joints, initial_placements,
    joint_residuals, modules, open_existing_or_new, solver_dof, version,
)
from freecad.KineSketch.utils.mechanisms.slider_crank import deg_to_rad


def _assemble_native(manifest: dict, paths: dict, output: Path, theta: float) -> tuple[dict, dict]:
    App, _, JointObject, _ = modules()
    previous = App.ActiveDocument.Name if App.ActiveDocument else None
    doc = None
    opened_sources = []
    try:
        doc = App.newDocument("KineSketchAssembly")
        assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
        check_methods(assembly, ("solve", "isPartGrounded", "isPartConnected"))
        assembly.Type = "Assembly"
        joint_group = assembly.newObject("Assembly::JointGroup", "Joints")
        parts = {}
        for role in ROLES:
            source, owned = open_existing_or_new(App, paths[role])
            if owned:
                opened_sources.append(source.Name)
            source_obj = source.getObject(manifest["parts"][role]["object_name"])
            if source_obj is None or not hasattr(source_obj, "Shape") or source_obj.Shape.isNull():
                raise MechanismError("SOURCE_OBJECT_MISSING", f"{role} object is missing or has no shape", "failed")
            if not source_obj.Shape.isValid() or not source_obj.Placement.isSame(App.Placement(), 1e-7):
                raise MechanismError("SOURCE_GEOMETRY_INVALID", f"{role} shape must be valid and have identity Placement", "failed")
            part = assembly.newObject("Part::Feature", role.title().replace("_", ""))
            part.Shape = source_obj.Shape.copy()
            part.Label = role
            parts[role] = part
        interfaces = {role: manifest["parts"][role]["interfaces"] for role in ROLES}
        radius = manifest["parameters"]["crank_radius_mm"]
        rod = manifest["parameters"]["rod_length_mm"]
        grounded, joints = create_joints(App, JointObject, assembly, joint_group, parts, interfaces)
        initial_placements(App, parts, radius, rod, theta)
        doc.recompute()
        solve_code = assembly.solve()
        if solve_code != 0:
            raise MechanismError("SOLVER_FAILED", f"FreeCAD Assembly.solve returned {solve_code}", "failed")
        residuals = joint_residuals(App, parts, interfaces)
        assert_residuals(residuals)
        if not assembly.isPartGrounded(parts["ground"]):
            raise MechanismError("GROUND_NOT_FIXED", "FreeCAD did not ground the base", "failed")
        for role in ROLES[1:]:
            if not assembly.isPartConnected(parts[role]):
                raise MechanismError("PART_DISCONNECTED", f"{role} is not connected to ground", "failed")
        dof = solver_dof(assembly)
        if dof["verified"] and dof["value"] != 1:
            raise MechanismError("UNEXPECTED_DOF", f"Expected one input DOF; FreeCAD reports {dof['value']}", "failed")
        assembly_path = output / "assembly.FCStd"
        doc.recompute()
        doc.saveAs(str(assembly_path))
        mechanism = {
            "schema_version": 1,
            "mechanism_type": "slider_crank",
            "units": {"length": "mm", "time": "s", "angle": "rad"},
            "coordinates": {"plane": "XY", "origin": "ground.main_axis", "slider_axis": "+X", "joint_axis": "+Z", "branch": "positive_slider_x"},
            "assembly_file": "assembly.FCStd",
            "assembly_sha256": sha256(assembly_path),
            "parameters": dict(manifest["parameters"]),
            "parts": {role: {"object_name": parts[role].Name, "interfaces": interfaces[role],
                              "source_object_name": manifest["parts"][role]["object_name"],
                              "source_sha256": manifest["parts"][role]["sha256"]} for role in ROLES},
            "fixed_role": "ground",
            "ground_object_name": grounded.Name,
            "joints": {joint_id: {"type": kind, "object_name": joints[joint_id].Name,
                                   "first": {"role": left, "interface": left_interface},
                                   "second": {"role": right, "interface": right_interface}}
                       for joint_id, kind, left, left_interface, right, right_interface in JOINTS},
            "initial_configuration": {"crank_angle_rad": theta, "branch": "positive_slider_x"},
            "driveable_joints": ["ground_crank"],
            "observable_points": {"slider_pin": {"role": "slider", "interface": "wrist_pin", "reference_origin": "ground.main_axis", "axis": "+X"}},
            "model_version": manifest.get("generator_version", "external"),
            "assembly_validation": {"status": "verified", "solver_return_code": solve_code,
                                     "joint_residuals": residuals, "dof": dof},
        }
        checks = {"solver_return_code": solve_code, "grounded": True,
                  "connected_roles": list(ROLES[1:]), "joint_residuals": residuals, "dof": dof,
                  "freecad_version": version(App),
                  "part_names": {role: parts[role].Name for role in ROLES},
                  "joint_names": {joint_id: joint.Name for joint_id, joint in joints.items()}}
        return mechanism, checks
    finally:
        for name in opened_sources:
            if App.getDocument(name):
                App.closeDocument(name)
        if doc is not None and App.getDocument(doc.Name):
            App.closeDocument(doc.Name)
        if previous and App.getDocument(previous):
            App.setActiveDocument(previous)


def run_assembly(request: dict) -> dict:
    """Return a structured result. Never overwrite an existing run directory."""
    output = None
    try:
        if not isinstance(request, dict):
            raise MechanismError("INVALID_REQUEST", "Assembly request must be a JSON object")
        if request.get("template") != "slider_crank":
            raise MechanismError("UNSUPPORTED_TEMPLATE", "template must be slider_crank")
        manifest_path = request.get("manifest_path")
        if not isinstance(manifest_path, str) or not manifest_path:
            raise MechanismError("MISSING_MANIFEST", "manifest_path is required")
        manifest_file = Path(manifest_path).expanduser().resolve()
        manifest = read_json(manifest_file)
        paths = validate_manifest(manifest, manifest_file.parent)
        if request.get("fixed_role", "ground") != "ground":
            raise MechanismError("INVALID_GROUND", "The supported mechanism grounds the ground role")
        theta = deg_to_rad(request.get("initial_angle_deg", 30))
        output = run_directory(request.get("output_dir"))
        output.mkdir(parents=True, exist_ok=False)
        mechanism, checks = _assemble_native(manifest, paths, output, theta)
        write_json(output / "mechanism.json", mechanism)
        warnings = [] if checks["dof"]["verified"] else ["Native DOF count is not exposed; one DOF remains unverified."]
        response = result("success", artifacts={"assembly": str(output / "assembly.FCStd"),
                                                "mechanism": str(output / "mechanism.json"),
                                                "report": str(output / "assembly_report.json")},
                          checks=checks, warnings=warnings)
    except MechanismError as exc:
        response = result(exc.status, checks=exc.checks, code=exc.code, message=str(exc))
    except Exception as exc:
        response = result("failed", code="ASSEMBLY_EXCEPTION", message=f"{type(exc).__name__}: {exc}")
    if output is not None and output.is_dir():
        report = {"skill": "assembly", "template": "slider_crank", "status": response["status"],
                  "parts": response["checks"].get("part_names", {}),
                  "created_joints": response["checks"].get("joint_names", {}),
                  "joint_plan": [j[0] for j in JOINTS],
                  "verified": ["solver", "grounded", "connected_roles", "joint_residuals"] if response["status"] == "success" else [],
                  "unverified": ["native_dof"] if response["status"] == "success" and not response["checks"]["dof"]["verified"] else [],
                  "checks": response["checks"], "warnings": response["warnings"], "error": response["error"]}
        write_json(output / "assembly_report.json", report)
        response["artifacts"]["report"] = str(output / "assembly_report.json")
    return response


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m freecad.KineSketch.skills.assembly.scripts.assembly request.json", file=sys.stderr)
        return 2
    try:
        request = read_json(Path(sys.argv[1]))
        response = run_assembly(request)
    except MechanismError as exc:
        response = result(exc.status, code=exc.code, message=str(exc))
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
