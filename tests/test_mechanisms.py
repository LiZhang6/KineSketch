"""Pure-Python contract and reference checks; no FreeCAD or mock solver."""

from __future__ import annotations

import importlib
import math
import shutil
import unittest
import uuid
from pathlib import Path

from models.mechanisms.slider_crank.generate_fixture import manifest_template
from freecad.KineSketch.skills.assembly.scripts.assembly import run_assembly
from freecad.KineSketch.skills.kinematic.scripts.kinematic import run_kinematic, validate_request
from freecad.KineSketch.utils.mechanisms.contract import (
    JOINTS, MechanismError, read_json, sha256, validate_manifest, validate_mechanism, write_json,
)
from freecad.KineSketch.utils.mechanisms.slider_crank import (
    deg_to_rad, differentiated, duration_s, rpm_to_rad_s, sample_times, slider_x_mm,
)


class ContractTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "outputs"
        scratch.mkdir(exist_ok=True)
        self.base = scratch / ("test_contract_" + uuid.uuid4().hex)
        self.base.mkdir()
        self.assertEqual(self.base.resolve().parent, scratch.resolve())
        self.addCleanup(shutil.rmtree, self.base)
        self.manifest = manifest_template(30, 120)
        for role, part in self.manifest["parts"].items():
            path = self.base / part["file"]
            path.write_bytes(("FCStd contract placeholder: " + role).encode())
            part["sha256"] = sha256(path)

    def test_manifest_and_resource_hash(self):
        self.assertEqual(len(validate_manifest(self.manifest, self.base)), 4)
        (self.base / "crank.FCStd").write_bytes(b"changed")
        with self.assertRaisesRegex(MechanismError, "checksum differs"):
            validate_manifest(self.manifest, self.base)

    def test_invalid_parameters_interfaces_and_units(self):
        for r, l in ((30, 30), (30, 10), (float("nan"), 120), (30, float("inf"))):
            with self.subTest(r=r, l=l), self.assertRaises(MechanismError):
                manifest_template(r, l)
        del self.manifest["parts"]["crank"]["interfaces"]["crank_pin"]
        with self.assertRaisesRegex(MechanismError, "required named interfaces"):
            validate_manifest(self.manifest, self.base)
        self.manifest["parts"]["crank"]["interfaces"]["crank_pin"] = {
            "origin_mm": [30, 0, 0], "rotation_xyzw": [0, 0, 0, 1]}
        self.manifest["units"]["length"] = "inch"
        with self.assertRaisesRegex(MechanismError, "units"):
            validate_manifest(self.manifest, self.base)

    def test_rejects_wrong_axis_offset_and_existing_run(self):
        self.manifest["parts"]["ground"]["interfaces"]["guide_axis"]["rotation_xyzw"] = [0, 0, 0, 1]
        with self.assertRaisesRegex(MechanismError, "conflicts"):
            validate_manifest(self.manifest, self.base)
        self.manifest["parts"]["ground"]["interfaces"]["guide_axis"]["rotation_xyzw"] = [0, 2**-0.5, 0, 2**-0.5]
        self.manifest["parameters"]["offset_mm"] = 1
        with self.assertRaisesRegex(MechanismError, "zero-offset"):
            validate_manifest(self.manifest, self.base)
        self.manifest["parameters"]["offset_mm"] = 0
        manifest_path = self.base / "parts_manifest.json"
        write_json(manifest_path, self.manifest)
        existing = self.base / "existing"
        existing.mkdir()
        response = run_assembly({"template": "slider_crank", "manifest_path": str(manifest_path),
                                 "output_dir": str(existing)})
        self.assertEqual(response["error"]["code"], "OUTPUT_EXISTS")

    def test_portable_paths_and_roundtrip(self):
        self.manifest["parts"]["ground"]["file"] = "../ground.FCStd"
        with self.assertRaisesRegex(MechanismError, "result package"):
            validate_manifest(self.manifest, self.base)
        self.manifest["parts"]["ground"]["file"] = "ground.FCStd"
        path = self.base / "manifest.json"
        write_json(path, self.manifest)
        self.assertEqual(read_json(path), self.manifest)

    def test_mechanism_requires_verified_unchanged_assembly(self):
        assembly = self.base / "assembly.FCStd"
        assembly.write_bytes(b"dummy validation target")
        mechanism = {
            "schema_version": 1, "mechanism_type": "slider_crank",
            "units": {"length": "mm", "time": "s", "angle": "rad"},
            "assembly_validation": {"status": "verified", "solver_return_code": 0}, "assembly_file": "assembly.FCStd",
            "assembly_sha256": sha256(assembly), "parameters": self.manifest["parameters"],
            "parts": {role: {"object_name": part["object_name"], "interfaces": part["interfaces"]}
                      for role, part in self.manifest["parts"].items()},
            "fixed_role": "ground", "driveable_joints": ["ground_crank"],
            "initial_configuration": {"crank_angle_rad": deg_to_rad(30)},
            "joints": {jid: {"type": kind, "object_name": "Joint_" + jid,
                              "first": {"role": a, "interface": ai}, "second": {"role": b, "interface": bi}}
                       for jid, kind, a, ai, b, bi in JOINTS},
        }
        self.assertEqual(validate_mechanism(mechanism, self.base), assembly)
        mechanism_file = self.base / "mechanism.json"
        write_json(mechanism_file, mechanism)
        request = {"mechanism_path": str(mechanism_file), "drive_joint": "ground_crank",
                   "rpm": 60, "cycles": 2, "sample_interval_s": 0.005,
                   "output_dir": str(self.base / "run")}
        _, _, _, omega, total, times, theta0 = validate_request(request)
        self.assertAlmostEqual(omega, 2 * math.pi)
        self.assertAlmostEqual(total, 2)
        self.assertEqual(len(times), 401)
        self.assertAlmostEqual(theta0, math.pi / 6)
        try:
            import FreeCAD  # noqa: F401
        except ImportError:
            blocked = run_kinematic(request)
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["error"]["code"], "FREECAD_UNAVAILABLE")
            self.assertFalse((self.base / "run" / "motion.csv").exists())
        assembly.write_bytes(b"changed")
        with self.assertRaisesRegex(MechanismError, "differs"):
            validate_mechanism(mechanism, self.base)

    def test_invalid_request_and_blocked_runtime(self):
        manifest_path = self.base / "parts_manifest.json"
        write_json(manifest_path, self.manifest)
        bad = run_assembly({"template": "slider_crank", "manifest_path": str(manifest_path),
                            "output_dir": str(self.base / "run"), "initial_angle_deg": float("nan")})
        self.assertEqual(bad["status"], "missing_input")
        self.assertFalse((self.base / "run").exists())
        try:
            import FreeCAD  # noqa: F401
        except ImportError:
            blocked = run_assembly({"template": "slider_crank", "manifest_path": str(manifest_path),
                                    "output_dir": str(self.base / "run")})
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["error"]["code"], "FREECAD_UNAVAILABLE")
            self.assertTrue((self.base / "run" / "assembly_report.json").is_file())
            self.assertFalse((self.base / "run" / "assembly.FCStd").exists())


class ReferenceTests(unittest.TestCase):
    def test_existing_frontend_headless_package_is_discoverable(self):
        frontend = importlib.import_module("freecad.KineSketch")
        self.assertEqual(Path(frontend.__file__).name, "__init__.py")
        self.assertEqual(Path(frontend.__file__).parent.name, "KineSketch")

    def test_speed_duration_and_dimensions(self):
        for radius, rod, rpm, cycles in ((30, 120, 60, 2), (42, 150, 90, 3)):
            with self.subTest(radius=radius, rpm=rpm):
                duration = duration_s(rpm, cycles)
                self.assertAlmostEqual(duration * rpm / 60, cycles)
                self.assertAlmostEqual(slider_x_mm(radius, rod, 0), rod + radius)
                self.assertAlmostEqual(slider_x_mm(radius, rod, math.pi), rod - radius)
                self.assertAlmostEqual((rod + radius) - (rod - radius), 2 * radius)
        self.assertAlmostEqual(rpm_to_rad_s(90), 3 * math.pi)
        with self.assertRaises(MechanismError):
            duration_s(0, 2)
        with self.assertRaises(MechanismError):
            rpm_to_rad_s(5e-324)
        with self.assertRaises(MechanismError):
            deg_to_rad(float("inf"))
        with self.assertRaisesRegex(MechanismError, "evenly divide"):
            sample_times(1, 0.03)

    def test_reference_formula_and_differentiation(self):
        times = sample_times(1, 0.01)
        values = [2 + 3 * t + 4 * t * t for t in times]
        velocity, acceleration = differentiated(times, values)
        for i in (0, 1, 50, 99, 100):
            self.assertAlmostEqual(velocity[i], 3 + 8 * times[i], places=7)
            self.assertAlmostEqual(acceleration[i], 8, places=6)
        with self.assertRaises(MechanismError):
            differentiated(times, values[:-1])


if __name__ == "__main__":
    unittest.main()
