# SPDX-License-Identifier: LGPL-2.1-or-later

"""A bounded, repeatable slider-crank demo driven by native FreeCAD Assembly."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from uuid import uuid4

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore

from freecad.KineSketch.utils.mechanisms.contract import ROLES
from freecad.KineSketch.utils.mechanisms.freecad_native import initial_placements


_player = None


def _number(arguments: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")
    value = float(value)
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _view_document(document) -> None:
    App.setActiveDocument(document.Name)
    view = Gui.activeDocument().activeView()
    view.viewAxonometric()
    view.fitAll()


def _parts_preview(manifest: dict, parts_dir: Path, run_dir: Path, angle: float) -> Path:
    """Show all four generated shapes before the native joints are built."""
    document = App.newDocument("SliderCrankParts")
    parts = {}
    try:
        for role in ROLES:
            source = App.openDocument(str(parts_dir / manifest["parts"][role]["file"]))
            try:
                source_obj = source.getObject(manifest["parts"][role]["object_name"])
                obj = document.addObject("Part::Feature", role.title().replace("_", ""))
                obj.Label = role.replace("_", " ").title()
                obj.Shape = source_obj.Shape.copy()
                parts[role] = obj
            finally:
                App.closeDocument(source.Name)
        initial_placements(
            App, parts, manifest["parameters"]["crank_radius_mm"],
            manifest["parameters"]["rod_length_mm"], math.radians(angle),
        )
        document.recompute()
        path = run_dir / "parts_preview.FCStd"
        document.saveAs(str(path))
        _view_document(document)
        return path
    except Exception:
        if App.getDocument(document.Name):
            App.closeDocument(document.Name)
        raise


class _NativePlayer:
    """Loop verified native solver frames in the visible FreeCAD document."""

    def __init__(self, document, checks: dict, fps: int, seconds: float) -> None:
        self.document = document
        self.assembly = document.getObject("Assembly")
        self.simulation = document.getObject("Simulation")
        if self.assembly is None or self.simulation is None:
            raise ValueError("Saved simulation is missing native Assembly objects")
        from freecad.KineSketch.utils.mechanisms.freecad_native import modules

        _, _, JointObject, _ = modules(simulation=True)
        for obj in document.Objects:
            if obj.Name.startswith("Joint_"):
                proxy = getattr(obj.ViewObject, "Proxy", None)
                if not callable(getattr(proxy, "redrawJointPlacements", None)):
                    JointObject.ViewProviderJoint(obj.ViewObject)
        for name in ("Ground", "Crank", "ConnectingRod", "Slider"):
            obj = document.getObject(name)
            if obj is None or obj.Shape.isNull():
                raise ValueError(f"Saved simulation is missing part {name}")
            obj.ViewObject.Visibility = True
        for name in ("Joints", "Simulations"):
            group = document.getObject(name)
            if group is not None:
                group.ViewObject.Visibility = False
        code = self.assembly.generateSimulation(self.simulation)
        if code != 0 or self.assembly.numberOfFrames() != checks["native_frame_count"]:
            raise ValueError("Native simulation changed after reopening the saved project")
        self.offset = checks["native_frame_offset"]
        self.samples = checks["frame_count"]
        self.fps = fps
        self.seconds = seconds
        self.tick = 0
        self.started = 0.0
        self.loop_frames = max(2, round(checks["requested_duration_s"] * fps / 0.5))
        self.timer = QtCore.QTimer(Gui.getMainWindow())
        self.timer.setInterval(max(16, round(1000 / fps)))
        self.timer.timeout.connect(self._advance)
        self.start_timer = QtCore.QTimer(Gui.getMainWindow())
        self.start_timer.setSingleShot(True)
        self.start_timer.timeout.connect(self.start)

    def schedule_start(self) -> None:
        self.start_timer.start(500)

    def start(self) -> None:
        _view_document(self.document)
        camera = Gui.activeDocument().activeView().getCameraNode()
        if hasattr(camera, "height"):
            camera.height.setValue(camera.height.getValue() * 0.65)
        self.started = time.monotonic()
        self.timer.start()

    def stop(self) -> None:
        self.start_timer.stop()
        self.timer.stop()

    def _advance(self) -> None:
        if (
            App.getDocument(self.document.Name) is None
            or App.ActiveDocument is None
            or App.ActiveDocument.Name != self.document.Name
            or time.monotonic() - self.started >= self.seconds
        ):
            self.stop()
            return
        phase = self.tick % self.loop_frames
        frame = self.offset + round(phase * (self.samples - 1) / (self.loop_frames - 1))
        self.assembly.updateForFrame(frame)
        self.tick += 1


def replay_slider_crank(seconds: float = 60) -> dict:
    global _player
    if _player is None or App.getDocument(_player.document.Name) is None:
        raise ValueError("No slider-crank simulation is loaded; create one first")
    _player.stop()
    _player.seconds = seconds
    _player.tick = 0
    _player.schedule_start()
    return {"ok": True, "document": _player.document.Name, "playback_seconds": seconds}


def create_slider_crank_demo(arguments: dict) -> dict:
    """Keep the original deterministic fixture path for explicit demo callers."""
    return _create_slider_crank(arguments, None)


def create_slider_crank_from_plan(arguments: dict) -> dict:
    """Use model-authored solids, then the verified assembly and motion skills."""
    for name in ("crank_radius_mm", "rod_length_mm", "parts"):
        if name not in arguments:
            raise ValueError(f"{name} is required for a model-authored mechanism")
    from freecad.KineSketch.skills.geometry.scripts.parts import validate_parts_plan
    from freecad.KineSketch.utils.mechanisms.contract import MechanismError

    try:
        plan = validate_parts_plan(arguments["parts"])
    except MechanismError as error:
        return {"ok": False, "stage": "plan", "error": f"{error.code}: {error}"}
    return _create_slider_crank(arguments, plan)


def _create_slider_crank(arguments: dict, parts_plan: dict | None) -> dict:
    """Generate, assemble, simulate, export, and play the supported mechanism."""
    global _player
    if not App.GuiUp:
        raise ValueError("The slider-crank demo requires FreeCAD GUI")
    radius = _number(arguments, "crank_radius_mm", 30, 1, 500)
    rod = _number(arguments, "rod_length_mm", 120, 2, 2000)
    if rod <= radius:
        raise ValueError("rod_length_mm must exceed crank_radius_mm")
    rpm = _number(arguments, "rpm", 60, 1, 240)
    cycles = _number(arguments, "cycles", 2, 0.1, 4)
    angle = _number(arguments, "initial_angle_deg", 30, -360, 360)
    seconds = _number(arguments, "playback_seconds", 60, 5, 300)
    duration = cycles * 60 / rpm
    if duration > 10:
        raise ValueError("This GUI demo is limited to 10 seconds of simulated motion")
    export_video = arguments.get("export_video", True)
    if not isinstance(export_video, bool):
        raise ValueError("export_video must be a boolean")
    if _player is not None:
        _player.stop()

    project_root = os.environ.get("KINESKETCH_PROJECT_ROOT")
    default_output = (Path(project_root) / "outputs" if project_root
                      else Path.home() / "Documents" / "KineSketch" / "outputs")
    output_parent = Path(os.environ.get("KINESKETCH_AGENT_OUTPUT_ROOT", default_output))
    output_parent.mkdir(parents=True, exist_ok=True)
    run_dir = output_parent / ("agent_slider_crank_" + uuid4().hex)
    run_dir.mkdir(exist_ok=False)
    response = {"ok": False, "run_directory": str(run_dir), "stage": "geometry",
                "source": "model_authored_features" if parts_plan is not None else "demo_fixture"}

    def checkpoint() -> None:
        (run_dir / "progress.json").write_text(
            json.dumps({"stage": response["stage"], "error": response.get("error")},
                       ensure_ascii=False), encoding="utf-8"
        )

    checkpoint()
    try:
        from freecad.KineSketch.skills.assembly.scripts.assembly import run_assembly
        from freecad.KineSketch.skills.kinematic.scripts.kinematic import run_kinematic
        from freecad.KineSketch.utils.mechanisms.contract import read_json

        if parts_plan is None:
            from freecad.KineSketch.skills.assembly.scripts.fixture import generate_fixture

            fixture = generate_fixture(str(run_dir / "parts"), radius, rod)
        else:
            from freecad.KineSketch.skills.geometry.scripts.parts import generate_parts_from_plan

            fixture = generate_parts_from_plan(str(run_dir / "parts"), radius, rod, parts_plan)
        if fixture["status"] != "success":
            return response | {"error": str(fixture)}
        manifest = read_json(Path(fixture["manifest"]))
        preview_path = _parts_preview(manifest, run_dir / "parts", run_dir, angle)
        response["geometry"] = {"parts": fixture["parts"], "manifest": fixture["manifest"],
                                "preview": str(preview_path)}
        if "design_plan" in fixture:
            response["geometry"]["design_plan"] = fixture["design_plan"]
        response["stage"] = "assembly"
        checkpoint()

        assembly = run_assembly({
            "template": "slider_crank", "manifest_path": fixture["manifest"],
            "fixed_role": "ground", "initial_angle_deg": angle,
            "output_dir": str(run_dir / "assembly"),
        })
        if assembly["status"] != "success":
            return response | {"error": assembly.get("error"), "report": assembly["artifacts"].get("report")}
        assembled_doc = App.openDocument(assembly["artifacts"]["assembly"])
        _view_document(assembled_doc)
        response["assembly"] = {"project": assembly["artifacts"]["assembly"],
                                "mechanism": assembly["artifacts"]["mechanism"],
                                "report": assembly["artifacts"]["report"],
                                "solver_return_code": assembly["checks"]["solver_return_code"]}
        response["stage"] = "simulation"
        checkpoint()

        motion = run_kinematic({
            "mechanism_path": assembly["artifacts"]["mechanism"],
            "drive_joint": "ground_crank", "rpm": rpm, "cycles": cycles,
            "sample_interval_s": duration / 400, "initial_angle_deg": angle,
            "playback_fps": 30, "output_dir": str(run_dir / "motion"),
        })
        if motion["status"] != "success":
            return response | {"error": motion.get("error"), "report": motion["artifacts"].get("report")}
        response["simulation"] = {"project": motion["artifacts"]["simulation.FCStd"],
                                  "csv": motion["artifacts"]["motion.csv"],
                                  "report": motion["artifacts"]["report"],
                                  "sampled_frames": motion["checks"]["frame_count"],
                                  "native_frames": motion["checks"]["native_frame_count"]}
        response["stage"] = "video"
        checkpoint()
        if export_video:
            from freecad.KineSketch.skills.kinematic.scripts.native_video import capture_native_frames
            from freecad.KineSketch.skills.kinematic.scripts.encode_video import encode_native_video

            capture = capture_native_frames({
                "simulation_path": motion["artifacts"]["simulation.FCStd"],
                "view": "axonometric", "fps": 30, "playback_speed": 0.5,
            })
            if capture["status"] != "success":
                return response | {"error": capture.get("error")}
            video = encode_native_video(capture["artifacts"]["capture_report"])
            if video["status"] != "success":
                return response | {"error": video.get("error")}
            response["video"] = {"mp4": video["artifacts"]["video"],
                                 "report": video["artifacts"]["report"],
                                 "verified_frames": video["checks"]["verified_frame_count"]}
        response["stage"] = "playback"
        checkpoint()
        simulation_doc = App.openDocument(motion["artifacts"]["simulation.FCStd"])
        _player = _NativePlayer(simulation_doc, motion["checks"], 30, seconds)
        _player.schedule_start()
        response["ok"] = True
        response["stage"] = "complete"
        response["playback_seconds"] = seconds
        response["active_document"] = simulation_doc.Name
        checkpoint()
        return response
    except Exception as error:
        response["error"] = f"{type(error).__name__}: {error}"
        checkpoint()
        return response
