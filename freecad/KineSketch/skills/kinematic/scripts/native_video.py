"""Capture actual FreeCAD Assembly solver frames from the GUI viewport."""

from __future__ import annotations

from pathlib import Path

from freecad.KineSketch.utils.mechanisms.contract import (
    MechanismError, number, read_json, result, sha256, write_json,
)
from freecad.KineSketch.utils.mechanisms.freecad_native import (
    modules, open_existing_or_new, version,
)


def _request(request: dict) -> tuple[Path, Path, dict, dict, str, int, float]:
    if not isinstance(request, dict):
        raise MechanismError("INVALID_REQUEST", "Video capture request must be a JSON object")
    location = request.get("simulation_path")
    if not isinstance(location, str) or not location:
        raise MechanismError("MISSING_SIMULATION", "simulation_path is required")
    simulation_path = Path(location).expanduser().resolve()
    if not simulation_path.is_file() or simulation_path.suffix.lower() != ".fcstd":
        raise MechanismError("MISSING_SIMULATION", f"FreeCAD simulation file is missing: {simulation_path}")
    output_value = request.get("output_path", str(simulation_path.with_name("freecad_native_3d.mp4")))
    if not isinstance(output_value, str) or not output_value:
        raise MechanismError("INVALID_OUTPUT", "output_path must be a nonempty MP4 path")
    output_path = Path(output_value).expanduser().resolve()
    if output_path.suffix.lower() != ".mp4" or not output_path.parent.is_dir():
        raise MechanismError("INVALID_OUTPUT", "output_path must be an MP4 in an existing directory")
    frames_path = output_path.with_name(output_path.stem + "_frames")
    capture_report = output_path.with_name(output_path.stem + ".capture.json")
    if output_path.exists() or frames_path.exists() or capture_report.exists():
        raise MechanismError("OUTPUT_EXISTS", f"Video output or capture files already exist beside {output_path}")
    view = request.get("view", "axonometric")
    if view not in ("axonometric", "top"):
        raise MechanismError("INVALID_VIEW", "view must be axonometric or top")
    fps = request.get("fps", 30)
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise MechanismError("INVALID_FPS", "fps must be an integer from 1 to 60")
    speed = number(request.get("playback_speed", 0.5), "playback_speed", positive=True)
    if not 0.1 <= speed <= 2:
        raise MechanismError("INVALID_SPEED", "playback_speed must be between 0.1 and 2")
    config = read_json(simulation_path.with_name("run_config.json"))
    report = read_json(simulation_path.with_name("simulation_report.json"))
    if report.get("status") != "success" or report.get("source") != "FreeCAD native Assembly simulation":
        raise MechanismError("SIMULATION_UNVERIFIED", "Only a successful native simulation can be captured")
    if config.get("simulation_project") != simulation_path.name:
        raise MechanismError("SIMULATION_UNVERIFIED", "run_config.json does not name this simulation")
    return simulation_path, output_path, config, report, view, fps, speed


def _capture(simulation_path: Path, output_path: Path, config: dict, report: dict,
             view_name: str, fps: int, speed: float) -> dict:
    App, _, JointObject, _ = modules(simulation=True)
    import FreeCADGui as Gui
    from PySide import QtWidgets

    previous = App.ActiveDocument.Name if App.ActiveDocument else None
    doc, owned = open_existing_or_new(App, simulation_path)
    part_placements = {}
    visibility = {}
    camera_state = None
    try:
        App.setActiveDocument(doc.Name)
        assembly = doc.getObject("Assembly")
        simulation = doc.getObject("Simulation")
        motion = doc.getObject("CrankMotion")
        if not all((assembly, simulation, motion)) or [obj.Name for obj in simulation.Group] != [motion.Name]:
            raise MechanismError("SIMULATION_CHANGED", "Saved FreeCAD simulation does not contain one CrankMotion", "failed")
        if motion.Formula != config.get("formula"):
            raise MechanismError("SIMULATION_CHANGED", "Saved drive formula differs from run_config.json", "failed")
        if abs(float(simulation.bTimeEnd.Value) - config["duration_s"]) > 1e-9 or abs(float(simulation.cTimeStepOutput.Value) - config["sample_interval_s"]) > 1e-9:
            raise MechanismError("SIMULATION_CHANGED", "Saved time settings differ from run_config.json", "failed")
        for obj in doc.Objects:
            if obj.Name.startswith("Joint_"):
                proxy = getattr(obj.ViewObject, "Proxy", None)
                if not callable(getattr(proxy, "redrawJointPlacements", None)):
                    JointObject.ViewProviderJoint(obj.ViewObject)
        for name in ("Ground", "Crank", "ConnectingRod", "Slider"):
            part = doc.getObject(name)
            if part is None or not hasattr(part, "Shape") or part.Shape.isNull():
                raise MechanismError("SIMULATION_CHANGED", f"Saved part {name} is missing", "failed")
            part_placements[name] = part.Placement
            visibility[name] = part.ViewObject.Visibility
            part.ViewObject.Visibility = True
        for name in ("Joints", "Simulations"):
            group = doc.getObject(name)
            if group:
                visibility[name] = group.ViewObject.Visibility
                group.ViewObject.Visibility = False

        code = assembly.generateSimulation(simulation)
        native_count = assembly.numberOfFrames()
        checks = report["checks"]
        sample_count = checks["frame_count"]
        frame_offset = checks["native_frame_offset"]
        if code != 0 or native_count != checks["native_frame_count"] or native_count != sample_count + frame_offset:
            raise MechanismError("SIMULATION_CHANGED", "Regenerated native frame count differs from the validated run", "failed")
        duration = number(config["duration_s"], "duration_s", positive=True)
        frame_count = max(2, round(duration * fps / speed))
        if frame_count > 3600:
            raise MechanismError("VIDEO_TOO_LONG", "Video capture is limited to 3600 frames")

        view = Gui.activeDocument().activeView()
        if callable(getattr(view, "getCamera", None)):
            camera_state = view.getCamera()
        assembly.updateForFrame(frame_offset)
        if view_name == "axonometric":
            view.viewAxonometric()
        else:
            view.viewTop()
        view.fitAll()
        camera = view.getCameraNode()
        if hasattr(camera, "height"):
            camera.height.setValue(camera.height.getValue() * 0.65)
        Gui.updateGui()
        QtWidgets.QApplication.processEvents()

        frames_path = output_path.with_name(output_path.stem + "_frames")
        frames_path.mkdir(exist_ok=False)
        first_digest = None
        changed = False
        for i in range(frame_count):
            index = frame_offset + round(i * (sample_count - 1) / (frame_count - 1))
            assembly.updateForFrame(index)
            Gui.updateGui()
            QtWidgets.QApplication.processEvents()
            frame_path = frames_path / f"frame_{i:04d}.png"
            view.saveImage(str(frame_path), 1280, 720, "White")
            if not frame_path.is_file() or frame_path.stat().st_size == 0:
                raise MechanismError("CAPTURE_FAILED", f"FreeCAD did not render frame {i}", "failed")
            if i == 0:
                first_digest = sha256(frame_path)
            elif not changed and sha256(frame_path) != first_digest:
                changed = True
        if not changed:
            raise MechanismError("CAPTURE_STATIC", "FreeCAD viewport frames did not change", "failed")
        details = {"source": "FreeCAD GUI activeView.saveImage", "freecad_version": version(App),
                   "generation_return_code": code, "native_frame_count": native_count,
                   "sampled_frame_count": sample_count, "video_frame_count": frame_count,
                   "duration_s": duration, "video_duration_s": frame_count / fps,
                   "fps": fps, "playback_speed": speed, "view": view_name,
                   "resolution": [1280, 720], "frames_dir": str(frames_path),
                   "frame_pattern": "frame_%04d.png", "target_video": str(output_path)}
        write_json(output_path.with_name(output_path.stem + ".capture.json"), details)
        return details
    finally:
        if doc is not None and App.getDocument(doc.Name):
            for name, placement in part_placements.items():
                obj = doc.getObject(name)
                if obj:
                    obj.Placement = placement
            for name, visible in visibility.items():
                obj = doc.getObject(name)
                if obj:
                    obj.ViewObject.Visibility = visible
            if camera_state is not None and not owned:
                try:
                    Gui.activeDocument().activeView().setCamera(camera_state)
                except Exception:
                    pass
            if owned:
                App.closeDocument(doc.Name)
        if previous and App.getDocument(previous):
            App.setActiveDocument(previous)


def capture_native_frames(request: dict) -> dict:
    """Recompute native frames and save actual FreeCAD viewport PNGs."""
    try:
        simulation_path, output_path, config, report, view, fps, speed = _request(request)
        checks = _capture(simulation_path, output_path, config, report, view, fps, speed)
        capture_report = output_path.with_name(output_path.stem + ".capture.json")
        first_frame = output_path.with_name(output_path.stem + "_frames") / "frame_0000.png"
        return result("success", artifacts={"capture_report": str(capture_report),
                                            "first_frame": str(first_frame)}, checks=checks)
    except MechanismError as exc:
        return result(exc.status, checks=exc.checks, code=exc.code, message=str(exc))
    except Exception as exc:
        return result("failed", code="CAPTURE_EXCEPTION", message=f"{type(exc).__name__}: {exc}")
