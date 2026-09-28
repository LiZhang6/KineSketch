"""Portable, strict input and output contracts for the first mechanism template."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA_VERSION = 1
ROLES = ("ground", "crank", "connecting_rod", "slider")
JOINTS = (
    ("ground_crank", "revolute", "ground", "main_axis", "crank", "main_axis"),
    ("crank_rod", "revolute", "crank", "crank_pin", "connecting_rod", "rod_big_end"),
    ("rod_slider", "revolute", "connecting_rod", "rod_small_end", "slider", "wrist_pin"),
    ("slider_ground", "slider", "slider", "guide_axis", "ground", "guide_axis"),
)
REQUIRED_INTERFACES = {
    "ground": ("main_axis", "guide_axis"),
    "crank": ("main_axis", "crank_pin"),
    "connecting_rod": ("rod_big_end", "rod_small_end"),
    "slider": ("wrist_pin", "guide_axis"),
}
IDENTITY = (0.0, 0.0, 0.0, 1.0)
GUIDE_ROTATION = (0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5))
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class MechanismError(Exception):
    def __init__(self, code: str, message: str, status: str = "missing_input", checks: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.checks = checks or {}


def number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MechanismError("INVALID_NUMBER", f"{field} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise MechanismError("INVALID_NUMBER", f"{field} must be finite" + (" and positive" if positive else ""))
    return value


def vector(values: object, field: str, size: int) -> tuple[float, ...]:
    if not isinstance(values, list) or len(values) != size:
        raise MechanismError("INVALID_INTERFACE", f"{field} must contain {size} numbers")
    return tuple(number(v, field) for v in values)


def relative_path(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MechanismError("INVALID_PATH", f"{field} must be a nonempty portable relative path")
    rel = Path(value)
    if rel.is_absolute() or any(p in ("..", ".") for p in rel.parts) or ":" in value:
        raise MechanismError("INVALID_PATH", f"{field} must stay in its result package")
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()):
        raise MechanismError("INVALID_PATH", f"{field} escapes its result package")
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MechanismError("FILE_MISSING", f"Missing file: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MechanismError("INVALID_JSON", f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MechanismError("INVALID_JSON", f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _check_frame(frame: object, field: str) -> None:
    if not isinstance(frame, dict):
        raise MechanismError("INVALID_INTERFACE", f"{field} must be an object")
    vector(frame.get("origin_mm"), f"{field}.origin_mm", 3)
    q = vector(frame.get("rotation_xyzw"), f"{field}.rotation_xyzw", 4)
    if abs(sum(v * v for v in q) - 1.0) > 1e-6:
        raise MechanismError("INVALID_INTERFACE", f"{field}.rotation_xyzw must be unit length")


def _near(actual: object, expected: tuple[float, ...], field: str) -> None:
    values = vector(actual, field, len(expected))
    if any(abs(a - b) > 1e-6 for a, b in zip(values, expected)):
        raise MechanismError("INVALID_INTERFACE", f"{field} conflicts with slider_crank parameters")


def validate_template_frames(parts: dict, radius: float, rod: float) -> None:
    expected = {
        "ground": {"main_axis": (0, 0, 0), "guide_axis": (0, 0, 0)},
        "crank": {"main_axis": (0, 0, 0), "crank_pin": (radius, 0, 0)},
        "connecting_rod": {"rod_big_end": (0, 0, 0), "rod_small_end": (rod, 0, 0)},
        "slider": {"wrist_pin": (0, 0, 0), "guide_axis": (0, 0, 0)},
    }
    for role, named in expected.items():
        for interface_name, location in named.items():
            frame = parts[role]["interfaces"][interface_name]
            _near(frame["origin_mm"], location, f"{role}.{interface_name}.origin_mm")
            orientation = GUIDE_ROTATION if interface_name == "guide_axis" else IDENTITY
            _near(frame["rotation_xyzw"], orientation, f"{role}.{interface_name}.rotation_xyzw")


def validate_manifest(data: dict, base: Path) -> dict[str, Path]:
    if type(data.get("schema_version")) is not int or data.get("schema_version") != SCHEMA_VERSION or data.get("mechanism_type") != "slider_crank":
        raise MechanismError("UNSUPPORTED_SCHEMA", "Expected slider_crank manifest schema_version 1")
    if data.get("units") != {"length": "mm", "angle": "rad"}:
        raise MechanismError("INVALID_UNITS", "Manifest units must be mm and rad")
    params = data.get("parameters")
    if not isinstance(params, dict):
        raise MechanismError("MISSING_PARAMETERS", "parameters object is required")
    r = number(params.get("crank_radius_mm"), "crank_radius_mm", positive=True)
    l = number(params.get("rod_length_mm"), "rod_length_mm", positive=True)
    if l <= r or number(params.get("offset_mm"), "offset_mm") != 0:
        raise MechanismError("UNSUPPORTED_GEOMETRY", "Only zero-offset mechanisms with rod_length_mm > crank_radius_mm are supported")
    parts = data.get("parts")
    if not isinstance(parts, dict) or set(parts) != set(ROLES):
        raise MechanismError("MISSING_PART", f"parts must contain exactly {', '.join(ROLES)}")
    paths = {}
    for role in ROLES:
        part = parts[role]
        if not isinstance(part, dict):
            raise MechanismError("MISSING_PART", f"parts.{role} must be an object")
        name = part.get("object_name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise MechanismError("INVALID_OBJECT_NAME", f"parts.{role}.object_name is invalid")
        path = relative_path(base, part.get("file"), f"parts.{role}.file")
        if path.suffix.lower() != ".fcstd" or not path.is_file():
            raise MechanismError("FILE_MISSING", f"parts.{role}.file must reference an existing FCStd file: {path}")
        digest = part.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MechanismError("MISSING_CHECKSUM", f"parts.{role}.sha256 is required")
        if sha256(path) != digest:
            raise MechanismError("RESOURCE_CHANGED", f"parts.{role}.file checksum differs; revalidate its interfaces")
        interfaces = part.get("interfaces")
        if not isinstance(interfaces, dict) or not set(REQUIRED_INTERFACES[role]).issubset(interfaces):
            raise MechanismError("MISSING_INTERFACE", f"parts.{role} lacks required named interfaces: {REQUIRED_INTERFACES[role]}")
        for interface_name, frame in interfaces.items():
            _check_frame(frame, f"parts.{role}.interfaces.{interface_name}")
        paths[role] = path
    validate_template_frames(parts, r, l)
    return paths


def validate_mechanism(data: dict, base: Path) -> Path:
    if type(data.get("schema_version")) is not int or data.get("schema_version") != SCHEMA_VERSION or data.get("mechanism_type") != "slider_crank":
        raise MechanismError("UNSUPPORTED_SCHEMA", "Expected slider_crank mechanism schema_version 1")
    if data.get("units") != {"length": "mm", "time": "s", "angle": "rad"}:
        raise MechanismError("INVALID_UNITS", "Mechanism units must be mm, s, rad")
    validation = data.get("assembly_validation")
    if (not isinstance(validation, dict) or validation.get("status") != "verified"
            or type(validation.get("solver_return_code")) is not int or validation["solver_return_code"] != 0):
        raise MechanismError("ASSEMBLY_UNVERIFIED", "kinematic requires a verified assembly")
    path = relative_path(base, data.get("assembly_file"), "assembly_file")
    if not path.is_file() or sha256(path) != data.get("assembly_sha256"):
        raise MechanismError("ASSEMBLY_CHANGED", "Assembly file is missing or differs from the validated assembly")
    parts = data.get("parts")
    if not isinstance(parts, dict) or set(parts) != set(ROLES):
        raise MechanismError("INVALID_MECHANISM", "Mechanism has invalid part roles")
    joints = data.get("joints")
    if not isinstance(joints, dict) or set(joints) != {j[0] for j in JOINTS}:
        raise MechanismError("INVALID_MECHANISM", "Mechanism has invalid joints")
    for joint_id, kind, left_role, left_iface, right_role, right_iface in JOINTS:
        joint = joints[joint_id]
        if not isinstance(joint, dict):
            raise MechanismError("INVALID_MECHANISM", f"Joint {joint_id} must be an object")
        if joint.get("type") != kind or joint.get("first") != {"role": left_role, "interface": left_iface} or joint.get("second") != {"role": right_role, "interface": right_iface}:
            raise MechanismError("INVALID_MECHANISM", f"Joint {joint_id} differs from the validated topology")
        if not isinstance(joint.get("object_name"), str) or not NAME_RE.fullmatch(joint["object_name"]):
            raise MechanismError("INVALID_MECHANISM", f"Joint {joint_id} object_name is invalid")
    if data.get("fixed_role") != "ground" or data.get("driveable_joints") != ["ground_crank"]:
        raise MechanismError("INVALID_MECHANISM", "Expected grounded base and one crank input")
    params = data.get("parameters")
    if not isinstance(params, dict):
        raise MechanismError("INVALID_MECHANISM", "Mechanism parameters are missing")
    r = number(params.get("crank_radius_mm"), "crank_radius_mm", positive=True)
    l = number(params.get("rod_length_mm"), "rod_length_mm", positive=True)
    if l <= r or number(params.get("offset_mm"), "offset_mm") != 0:
        raise MechanismError("UNSUPPORTED_GEOMETRY", "Mechanism dimensions are outside the supported zero-offset range")
    initial = data.get("initial_configuration")
    if not isinstance(initial, dict):
        raise MechanismError("INVALID_MECHANISM", "Initial configuration is missing")
    number(initial.get("crank_angle_rad"), "crank_angle_rad")
    for role in ROLES:
        part = parts[role]
        if not isinstance(part, dict):
            raise MechanismError("INVALID_MECHANISM", f"Part {role} must be an object")
        if not isinstance(part.get("object_name"), str) or not NAME_RE.fullmatch(part["object_name"]):
            raise MechanismError("INVALID_MECHANISM", f"Part {role} object_name is invalid")
        if not isinstance(part.get("interfaces"), dict):
            raise MechanismError("INVALID_MECHANISM", f"Part {role} has no interfaces")
        for interface_name in REQUIRED_INTERFACES[role]:
            _check_frame(part["interfaces"].get(interface_name), f"{role}.{interface_name}")
    validate_template_frames(parts, r, l)
    return path


def run_directory(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MechanismError("MISSING_OUTPUT", "output_dir must be a new run directory")
    path = Path(value).expanduser().resolve()
    if path.exists():
        raise MechanismError("OUTPUT_EXISTS", f"Output directory already exists: {path}")
    return path


def result(status: str, *, artifacts: dict | None = None, checks: dict | None = None,
           warnings: list | None = None, code: str | None = None, message: str | None = None) -> dict:
    return {"status": status, "artifacts": artifacts or {}, "checks": checks or {},
            "warnings": warnings or [], "error": {"code": code, "message": message} if code else None}
