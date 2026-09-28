"""Small adapter for the built-in FreeCAD Assembly workbench (lazy imports)."""

from __future__ import annotations

import math
from pathlib import Path

from .contract import JOINTS, MechanismError, ROLES


def modules(*, simulation: bool = False):
    try:
        import FreeCAD as App
        import Part
        import JointObject
        if simulation and not App.GuiUp:
            raise MechanismError(
                "FREECAD_GUI_REQUIRED",
                "Native Assembly simulation must run inside the FreeCAD GUI Python environment",
                "blocked",
            )
        if simulation:
            import CommandCreateSimulation
    except ImportError as exc:
        raise MechanismError(
            "FREECAD_UNAVAILABLE",
            f"FreeCAD with its built-in Assembly workbench is unavailable in this Python runtime: {exc}",
            "blocked",
        ) from exc
    return (App, Part, JointObject, CommandCreateSimulation if simulation else None)


def version(App) -> str:
    try:
        return ".".join(str(x) for x in App.Version()[:3])
    except Exception:
        return "unknown"


def check_methods(assembly, names: tuple[str, ...]) -> None:
    missing = [name for name in names if not callable(getattr(assembly, name, None))]
    if missing:
        raise MechanismError("ASSEMBLY_CAPABILITY_MISSING", f"This FreeCAD Assembly object lacks: {', '.join(missing)}", "blocked")


def open_existing_or_new(App, path: Path):
    """Return a source document and whether this call opened it."""
    for doc in App.listDocuments().values():
        filename = getattr(doc, "FileName", "")
        if filename and Path(filename).resolve() == path.resolve():
            return doc, False
    return App.openDocument(str(path)), True


def frame_placement(App, frame: dict):
    x, y, z = frame["origin_mm"]
    qx, qy, qz, qw = frame["rotation_xyzw"]
    return App.Placement(App.Vector(x, y, z), App.Rotation(qx, qy, qz, qw))


def initial_placements(App, parts: dict, radius: float, rod: float, theta: float):
    from .slider_crank import rod_angle_rad, slider_x_mm

    px, py = radius * math.cos(theta), radius * math.sin(theta)
    parts["ground"].Placement = App.Placement()
    parts["crank"].Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 0, 1), math.degrees(theta)))
    parts["connecting_rod"].Placement = App.Placement(
        App.Vector(px, py, 0), App.Rotation(App.Vector(0, 0, 1), math.degrees(rod_angle_rad(radius, rod, theta)))
    )
    parts["slider"].Placement = App.Placement(App.Vector(slider_x_mm(radius, rod, theta), 0, 0), App.Rotation())


def create_joints(App, JointObject, assembly, group, parts: dict, interfaces: dict) -> dict:
    grounded = group.newObject("App::FeaturePython", "GroundedGround")
    JointObject.GroundedJoint(grounded, parts["ground"])
    if App.GuiUp:
        JointObject.ViewProviderGroundedJoint(grounded.ViewObject)
    joints = {}
    for joint_id, kind, first_role, first_name, second_role, second_name in JOINTS:
        joint = group.newObject("App::FeaturePython", "Joint_" + joint_id)
        JointObject.Joint(joint, 1 if kind == "revolute" else 3)
        if App.GuiUp:
            JointObject.ViewProviderJoint(joint.ViewObject)
        joint.Suppressed = True
        joint.Detach1 = True
        joint.Detach2 = True
        joint.Reference1 = [parts[first_role], ["", ""]]
        joint.Reference2 = [parts[second_role], ["", ""]]
        joint.Placement1 = frame_placement(App, interfaces[first_role][first_name])
        joint.Placement2 = frame_placement(App, interfaces[second_role][second_name])
        joint.Suppressed = False
        joints[joint_id] = joint
    return grounded, joints


def joint_residuals(App, parts: dict, interfaces: dict) -> dict[str, dict[str, float]]:
    results = {}
    for joint_id, kind, first_role, first_name, second_role, second_name in JOINTS:
        first = parts[first_role].Placement * frame_placement(App, interfaces[first_role][first_name])
        second = parts[second_role].Placement * frame_placement(App, interfaces[second_role][second_name])
        delta = second.Base - first.Base
        axis1 = first.Rotation.multVec(App.Vector(0, 0, 1))
        axis2 = second.Rotation.multVec(App.Vector(0, 0, 1))
        if kind == "slider":
            distance = (delta - axis1 * delta.dot(axis1)).Length
        else:
            distance = delta.Length
        results[joint_id] = {
            "point_or_lateral_error_mm": float(distance),
            "axis_parallel_error": float(1 - abs(axis1.dot(axis2))),
        }
    return results


def assert_residuals(residuals: dict, *, length_tolerance_mm: float = 0.1, axis_tolerance: float = 1e-4) -> None:
    for joint_id, values in residuals.items():
        if values["point_or_lateral_error_mm"] > length_tolerance_mm or values["axis_parallel_error"] > axis_tolerance:
            raise MechanismError("JOINT_RESIDUAL", f"Joint {joint_id} is inconsistent after solving: {values}", "failed")


def solver_dof(assembly) -> dict:
    for name in ("DoF", "DOF", "DegreesOfFreedom"):
        value = getattr(assembly, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return {"value": value, "source": f"FreeCAD Assembly.{name}", "verified": True}
    return {"value": None, "source": "not exposed by this FreeCAD Python object", "verified": False}


def validate_doc_objects(doc, mechanism: dict):
    parts = {}
    for role in ROLES:
        name = mechanism["parts"][role]["object_name"]
        obj = doc.getObject(name)
        if obj is None or not hasattr(obj, "Shape") or obj.Shape.isNull():
            raise MechanismError("ASSEMBLY_OBJECT_MISSING", f"Assembly part {role}/{name} is missing or has no shape", "failed")
        parts[role] = obj
    joints = {}
    for joint_id, joint_data in mechanism["joints"].items():
        obj = doc.getObject(joint_data["object_name"])
        if obj is None or not hasattr(obj, "JointType"):
            raise MechanismError("ASSEMBLY_JOINT_MISSING", f"Assembly joint {joint_id} is missing", "failed")
        expected = "Revolute" if joint_data["type"] == "revolute" else "Slider"
        if obj.JointType != expected or obj.Reference1[0] != parts[joint_data["first"]["role"]] or obj.Reference2[0] != parts[joint_data["second"]["role"]]:
            raise MechanismError("ASSEMBLY_JOINT_CHANGED", f"Assembly joint {joint_id} no longer matches its contract", "failed")
        joints[joint_id] = obj
    return parts, joints
