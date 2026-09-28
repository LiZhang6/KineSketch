"""Drive one FreeCAD Assembly joint and measure solver-generated placements."""

from __future__ import annotations

import csv
import json
import math
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from freecad.KineSketch.utils.mechanisms.contract import (
    MechanismError, number, read_json, result, run_directory, validate_mechanism, write_json,
)
from freecad.KineSketch.utils.mechanisms.freecad_native import (
    assert_residuals, check_methods, joint_residuals, modules, open_existing_or_new,
    validate_doc_objects, version,
)
from freecad.KineSketch.utils.mechanisms.slider_crank import (
    deg_to_rad, differentiated, duration_s, rpm_to_rad_s, sample_times, slider_x_mm,
)


def validate_request(request: dict) -> tuple[dict, Path, Path, float, float, list[float], float]:
    if not isinstance(request, dict):
        raise MechanismError("INVALID_REQUEST", "Kinematic request must be a JSON object")
    location = request.get("mechanism_path")
    if not isinstance(location, str) or not location:
        raise MechanismError("MISSING_MECHANISM", "mechanism_path is required")
    mechanism_path = Path(location).expanduser().resolve()
    mechanism = read_json(mechanism_path)
    assembly_path = validate_mechanism(mechanism, mechanism_path.parent)
    if request.get("drive_joint") != "ground_crank":
        raise MechanismError("INVALID_DRIVE", "Only ground_crank may be driven")
    observation = request.get("observation", {"role": "slider", "point": "wrist_pin", "frame": "ground"})
    if observation != {"role": "slider", "point": "wrist_pin", "frame": "ground"}:
        raise MechanismError("INVALID_OBSERVATION", "Only slider.wrist_pin along ground +X is supported")
    rpm = number(request.get("rpm"), "rpm", positive=True)
    omega = rpm_to_rad_s(rpm)
    if ("cycles" in request) == ("duration_s" in request):
        raise MechanismError("INVALID_DURATION", "Provide exactly one of cycles or duration_s")
    total = duration_s(rpm, request["cycles"]) if "cycles" in request else number(request["duration_s"], "duration_s", positive=True)
    times = sample_times(total, request.get("sample_interval_s"))
    theta0 = number(mechanism["initial_configuration"]["crank_angle_rad"], "initial crank angle")
    if "initial_angle_deg" in request and abs(deg_to_rad(request["initial_angle_deg"]) - theta0) > 1e-8:
        raise MechanismError("INITIAL_CONDITION_MISMATCH", "Initial angle differs from assembly; rerun assembly for a different start")
    output = run_directory(request.get("output_dir"))
    return mechanism, mechanism_path, assembly_path, omega, total, times, theta0


def _measure(App, parts: dict, mechanism: dict, expected_theta: float) -> tuple[float, float]:
    slider = parts["slider"]
    ground = parts["ground"]
    slider_origin = mechanism["parts"]["slider"]["interfaces"]["wrist_pin"]["origin_mm"]
    ground_origin = mechanism["parts"]["ground"]["interfaces"]["main_axis"]["origin_mm"]
    point = slider.Placement.multVec(App.Vector(*slider_origin))
    origin = ground.Placement.multVec(App.Vector(*ground_origin))
    axis = ground.Placement.Rotation.multVec(App.Vector(1, 0, 0))
    x = float((point - origin).dot(axis))
    crank_x = parts["crank"].Placement.Rotation.multVec(App.Vector(1, 0, 0))
    raw_theta = math.atan2(crank_x.y, crank_x.x)
    theta = raw_theta + 2 * math.pi * round((expected_theta - raw_theta) / (2 * math.pi))
    return theta, x


def _motion_svg(path: Path, times: list[float], positions: list[float]) -> None:
    width, height, margin = 800, 300, 45
    x0, x1 = times[0], times[-1]
    y0, y1 = min(positions), max(positions)
    if y1 == y0:
        y1 += 1
    points = " ".join(
        f"{margin + (t - x0) / (x1 - x0) * (width - 2 * margin):.2f},"
        f"{height - margin - (v - y0) / (y1 - y0) * (height - 2 * margin):.2f}"
        for t, v in zip(times, positions)
    )
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
           '<rect width="100%" height="100%" fill="white"/>'
           f'<path d="M {margin},{margin} V {height-margin} H {width-margin}" fill="none" stroke="#555"/>'
           f'<polyline points="{points}" fill="none" stroke="#0066aa" stroke-width="2"/>'
           f'<text x="{width//2}" y="{height-8}" text-anchor="middle" font-family="sans-serif" font-size="13">time (s)</text>'
           '<text x="12" y="20" font-family="sans-serif" font-size="13">slider position (mm)</text>'
           '</svg>\n')
    path.write_text(svg, encoding="utf-8")


def _simulate_native(mechanism: dict, assembly_path: Path, output: Path,
                     omega: float, total: float, times: list[float], theta0: float,
                     fps: int) -> tuple[dict, dict]:
    App, _, JointObject, simulation_module = modules(simulation=True)
    previous = App.ActiveDocument.Name if App.ActiveDocument else None
    source_copy = output / "assembly.FCStd"
    shutil.copy2(assembly_path, source_copy)
    doc, owned = open_existing_or_new(App, source_copy)
    try:
        assembly = doc.getObject("Assembly")
        if assembly is None:
            raise MechanismError("ASSEMBLY_OBJECT_MISSING", "Native Assembly object is missing", "failed")
        check_methods(assembly, ("solve", "generateSimulation", "numberOfFrames", "updateForFrame"))
        parts, joints = validate_doc_objects(doc, mechanism)
        if App.GuiUp:
            for joint in joints.values():
                view = joint.ViewObject
                if not callable(getattr(getattr(view, "Proxy", None), "redrawJointPlacements", None)):
                    JointObject.ViewProviderJoint(view)
        if assembly.solve() != 0:
            raise MechanismError("SOLVER_FAILED", "Reopened assembly failed to solve", "failed")
        assert_residuals(joint_residuals(App, parts, {r: mechanism["parts"][r]["interfaces"] for r in parts}))
        group = assembly.newObject("Assembly::SimulationGroup", "Simulations")
        sim = group.newObject("App::FeaturePython", "Simulation")
        simulation_module.Simulation(sim)
        sim.aTimeStart = 0.0
        sim.bTimeEnd = total
        sim.cTimeStepOutput = times[1] - times[0]
        sim.jFramesPerSecond = fps
        motion = assembly.newObject("App::FeaturePython", "CrankMotion")
        formula = f"{theta0:.17g} + {omega:.17g}*time"
        simulation_module.Motion(motion, "Angular", joints["ground_crank"], formula)
        sim.Group = [motion]
        doc.recompute()
        generation_code = assembly.generateSimulation(sim)
        if generation_code != 0:
            raise MechanismError("SIMULATION_FAILED", f"FreeCAD generateSimulation returned {generation_code}", "failed")
        frame_count = assembly.numberOfFrames()
        if frame_count not in (len(times), len(times) + 1):
            raise MechanismError("FRAME_COUNT_MISMATCH", f"FreeCAD returned {frame_count} frames; expected {len(times)} or one extra initial frame", "failed",
                                 {"native_frame_count": frame_count, "expected_frame_count": len(times)})
        frame_offset = frame_count - len(times)
        interfaces = {role: mechanism["parts"][role]["interfaces"] for role in parts}
        angles, positions = [], []
        max_joint_error = 0.0
        max_axis_error = 0.0
        for index, time_s in enumerate(times):
            assembly.updateForFrame(index + frame_offset)
            theta, x = _measure(App, parts, mechanism, theta0 + omega * time_s)
            if not math.isfinite(theta) or not math.isfinite(x):
                raise MechanismError("NONFINITE_RESULT", f"Nonfinite solver placement at frame {index}", "failed")
            residuals = joint_residuals(App, parts, interfaces)
            max_joint_error = max(max_joint_error, *(v["point_or_lateral_error_mm"] for v in residuals.values()))
            max_axis_error = max(max_axis_error, *(v["axis_parallel_error"] for v in residuals.values()))
            angles.append(theta)
            positions.append(x)
        velocities, accelerations = differentiated(times, positions)
        r = mechanism["parameters"]["crank_radius_mm"]
        l = mechanism["parameters"]["rod_length_mm"]
        expected_positions = [slider_x_mm(r, l, theta0 + omega * t) for t in times]
        max_reference_error = max(abs(x - ref) for x, ref in zip(positions, expected_positions))
        max_drive_error = max(abs(theta - (theta0 + omega * t)) for theta, t in zip(angles, times))
        max_jump = max(abs(b - a) for a, b in zip(positions, positions[1:]))
        maximum_step = max(b - a for a, b in zip(times, times[1:]))
        jump_bound = 2 * (r + r * r / math.sqrt(l * l - r * r)) * omega * maximum_step + 0.25
        in_range = all(l - r - 0.25 <= x <= l + r + 0.25 for x in positions)
        completed = abs(times[-1] - total) <= 1e-9
        checks = {
            "completed_duration_s": times[-1], "requested_duration_s": total,
            "frame_count": len(times), "native_frame_count": frame_count,
            "native_frame_offset": frame_offset,
            "generation_return_code": generation_code,
            "max_joint_error_mm": max_joint_error, "max_axis_parallel_error": max_axis_error,
            "max_reference_position_error_mm": max_reference_error,
            "max_drive_angle_error_rad": max_drive_error, "max_position_step_mm": max_jump,
            "position_step_bound_mm": jump_bound, "theoretical_stroke_mm": 2 * r,
            "observed_sampled_range_mm": max(positions) - min(positions),
            "freecad_version": version(App),
            "completed": completed, "inside_theoretical_range": in_range,
        }
        with (output / "motion.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("time_s", "crank_angle_rad", "slider_position_mm", "slider_displacement_from_initial_mm", "slider_velocity_mm_s", "slider_acceleration_mm_s2"))
            for values in zip(times, angles, positions, (x - positions[0] for x in positions), velocities, accelerations):
                writer.writerow((f"{v:.12g}" for v in values))
        _motion_svg(output / "motion.svg", times, positions)
        write_json(output / "run_config.json", {
            "schema_version": 1, "mechanism_file": "mechanism.json", "simulation_project": "simulation.FCStd",
            "drive_joint": "ground_crank", "drive_type": "constant_rpm", "omega_rad_s": omega,
            "initial_angle_rad": theta0, "formula": formula, "duration_s": total,
            "sample_interval_s": times[1] - times[0], "playback_fps": fps,
            "observation": {"role": "slider", "point": "wrist_pin", "frame": "ground", "axis": "+X"},
            "position_origin": "ground.main_axis", "position_unit": "mm",
            "velocity_source": "five-sample local quadratic fit to native solver positions",
            "acceleration_source": "five-sample local quadratic fit to native solver positions",
            "endpoint_handling": "shifted five-sample window",
        })
        assembly.updateForFrame(frame_offset)
        doc.recompute()
        doc.saveAs(str(output / "simulation.FCStd"))
        if not (completed and in_range and max_joint_error <= 0.1 and max_axis_error <= 1e-4
                and max_reference_error <= 0.25 and max_drive_error <= 0.005 and max_jump <= jump_bound):
            raise MechanismError("RESULT_VALIDATION_FAILED", "Native frames failed one or more motion checks", "failed", checks)
        artifacts = {name: str(output / name) for name in
                     ("assembly.FCStd", "mechanism.json", "simulation.FCStd", "run_config.json", "motion.csv", "motion.svg")}
        return artifacts, checks
    finally:
        if owned and App.getDocument(doc.Name):
            App.closeDocument(doc.Name)
        if previous and App.getDocument(previous):
            App.setActiveDocument(previous)


def run_kinematic(request: dict) -> dict:
    """Use FreeCAD's native simulation; a missing native capability is a blocked result."""
    output = None
    try:
        mechanism, mechanism_path, assembly_path, omega, total, times, theta0 = validate_request(request)
        fps = request.get("playback_fps", 30)
        if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 240:
            raise MechanismError("INVALID_PLAYBACK_FPS", "playback_fps must be an integer from 1 to 240")
        output = run_directory(request["output_dir"])
        output.mkdir(parents=True, exist_ok=False)
        shutil.copy2(mechanism_path, output / "mechanism.json")
        artifacts, checks = _simulate_native(mechanism, assembly_path, output, omega, total, times, theta0, fps)
        response = result("success", artifacts=artifacts, checks=checks)
    except MechanismError as exc:
        response = result(exc.status, checks=exc.checks, code=exc.code, message=str(exc))
    except Exception as exc:
        response = result("failed", code="KINEMATIC_EXCEPTION", message=f"{type(exc).__name__}: {exc}")
    if output is not None and output.is_dir():
        report = {"skill": "kinematic", "status": response["status"], "source": "FreeCAD native Assembly simulation" if response["status"] == "success" else "no verified result",
                  "checks": response["checks"], "warnings": response["warnings"], "error": response["error"],
                  "measurement": {"origin": "ground.main_axis", "axis": "+X", "position_unit": "mm",
                                  "time_unit": "s", "angle_unit": "rad", "displacement": "relative to first solver frame",
                                  "velocity_acceleration": "five-sample quadratic fit to solver positions; shifted window at endpoints"}}
        write_json(output / "simulation_report.json", report)
        response["artifacts"]["report"] = str(output / "simulation_report.json")
        for name in ("assembly.FCStd", "mechanism.json", "simulation.FCStd", "run_config.json", "motion.csv", "motion.svg"):
            if (output / name).is_file():
                response["artifacts"].setdefault(name, str(output / name))
    return response


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m freecad.KineSketch.skills.kinematic.scripts.kinematic request.json", file=sys.stderr)
        return 2
    try:
        request = read_json(Path(sys.argv[1]))
        response = run_kinematic(request)
    except MechanismError as exc:
        response = result(exc.status, code=exc.code, message=str(exc))
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
