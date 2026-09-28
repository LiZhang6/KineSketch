# Constant-speed experiment

Python entry point: `freecad.KineSketch.skills.kinematic.scripts.kinematic.run_kinematic(request)`. CLI: `python -m freecad.KineSketch.skills.kinematic.scripts.kinematic request.json` inside FreeCAD's Python environment.

```json
{
  "mechanism_path": "C:/project/outputs/assembly_001/mechanism.json",
  "drive_joint": "ground_crank",
  "rpm": 60,
  "cycles": 2,
  "sample_interval_s": 0.005,
  "initial_angle_deg": 30,
  "observation": {"role": "slider", "point": "wrist_pin", "frame": "ground"},
  "playback_fps": 30,
  "output_dir": "C:/project/outputs/motion_001"
}
```

Use exactly one of `cycles` and `duration_s`. Runtime is `cycles*60/rpm` seconds. Choose a `sample_interval_s` that evenly divides this duration. The initial angle must match the assembly; rerun assembly to change it. `sample_interval_s` governs data; `playback_fps` governs animation only. Run native simulation inside the FreeCAD GUI Python environment. FreeCAD 1.2 adds one duplicate initial frame; the backend samples the next frame as time zero and reports both native and sampled frame counts. Other frame-count differences fail validation.

`motion.csv` contains `time_s`, measured `crank_angle_rad`, `slider_position_mm` from `ground.main_axis` along ground +X, displacement relative to the first solver frame, velocity in mm/s and acceleration in mm/s². The latter two are five-sample local quadratic fits to the solver positions; endpoints use shifted windows. The analytical slider-crank formula is used only for comparison. `simulation_report.json` includes solver/frame status, joint errors, jump checks, range, and sample-time comparison to that reference. Theoretical stroke is `2*crank_radius_mm`; sampled extrema may not hit it exactly.

## Optional real FreeCAD viewport video

Only after `run_kinematic` returns `success`, call this inside FreeCAD's GUI Python process:

```python
from freecad.KineSketch.skills.kinematic.scripts.native_video import capture_native_frames
captured = capture_native_frames({
    "simulation_path": simulated["artifacts"]["simulation.FCStd"],
    "view": "axonometric", "fps": 30, "playback_speed": 0.5,
})
```

The capture reopens the saved project, regenerates its native Assembly frames, calls `assembly.updateForFrame` for each selected frame, and saves images with FreeCAD's GUI viewport. It checks that the viewport frames change. After the FreeCAD GUI process exits, encode its `capture_report` with the Pixi Python environment:

```powershell
.\.pixi\bin\pixi.exe run --as-is python -m freecad.KineSketch.skills.kinematic.scripts.encode_video "C:\path\to\motion\freecad_native_3d.capture.json"
```

`encode_native_video` verifies the H.264 frame count and resolution, then returns the MP4 and its report. The default 2 s native run becomes a 4 s video at 0.5× speed, 30 fps, 1280×720. Captured PNGs remain beside the video for inspection. Run `pixi run demo-native` on Windows for a fresh end-to-end example with no manual console calls. Video export is optional; failure must not be reported as a successful video artifact.
