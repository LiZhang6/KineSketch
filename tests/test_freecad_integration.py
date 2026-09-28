"""Real FreeCAD integration; skipped when FreeCAD cannot be imported."""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from models.mechanisms.slider_crank.generate_fixture import generate_fixture
from freecad.KineSketch.skills.assembly.scripts.assembly import run_assembly
from freecad.KineSketch.skills.kinematic.scripts.kinematic import run_kinematic

try:
    import FreeCAD
    HAS_FREECAD_GUI = bool(FreeCAD.GuiUp)
except ImportError:
    HAS_FREECAD_GUI = False

def _parts_manifest_from_test_request():
    report = os.environ.get("KINESKETCH_TEST_REPORT")
    if not report:
        return None
    request = Path(report).with_name("parts_request.json")
    if not request.is_file():
        return None
    return json.loads(request.read_text(encoding="utf-8-sig"))["manifest_path"]


PARTS_MANIFEST = _parts_manifest_from_test_request()


@unittest.skipUnless(HAS_FREECAD_GUI, "FreeCAD GUI Python runtime is required for native simulation")
class NativeIntegrationTests(unittest.TestCase):
    def test_fixture_or_external_parts_assembly_reopen_and_motion_profiles(self):
        scratch = Path(__file__).resolve().parents[1] / "outputs"
        scratch.mkdir(exist_ok=True)
        base = scratch / ("test_native_" + uuid.uuid4().hex)
        base.mkdir()
        self.assertEqual(base.resolve().parent, scratch.resolve())
        try:
            for key, radius, rod, rpm, cycles in (("default", 30, 120, 60, 2),
                                                   ("variant", 42, 150, 90, 3)):
                with self.subTest(key=key):
                    if PARTS_MANIFEST:
                        manifest = Path(PARTS_MANIFEST).expanduser().resolve()
                        self.assertTrue(manifest.is_file(), manifest)
                    else:
                        dimensions = () if key == "default" else (radius, rod)
                        fixture = generate_fixture(str(base / (key + "_fixture")), *dimensions)
                        self.assertEqual(fixture["status"], "success")
                        manifest = Path(fixture["manifest"])
                    assembled = run_assembly({"template": "slider_crank", "manifest_path": str(manifest),
                                              "initial_angle_deg": 30, "output_dir": str(base / (key + "_assembly"))})
                    self.assertEqual(assembled["status"], "success", assembled)
                    self.assertTrue(Path(assembled["artifacts"]["assembly"]).is_file())
                    simulated = run_kinematic({"mechanism_path": assembled["artifacts"]["mechanism"],
                                               "drive_joint": "ground_crank", "rpm": rpm, "cycles": cycles,
                                               "sample_interval_s": 0.005, "output_dir": str(base / (key + "_motion"))})
                    self.assertEqual(simulated["status"], "success", simulated)
                    self.assertTrue(Path(simulated["artifacts"]["motion.csv"]).is_file())
                    report = json.loads(Path(simulated["artifacts"]["report"]).read_text(encoding="utf-8"))
                    self.assertEqual(report["checks"]["frame_count"], 401)
        finally:
            shutil.rmtree(base)


if __name__ == "__main__":
    unittest.main()
