---
name: kinesketch-kinematic
description: Run a constant-rpm native FreeCAD slider-crank simulation from a verified KineSketch mechanism contract, with optional real FreeCAD viewport video; use when motion data, checks, or a visual preview are needed.
---

# Kinematic

Use this skill after the assembly skill produced a verified `mechanism.json` and `assembly.FCStd`. Run it inside FreeCAD's GUI Python process because the native simulation module requires GUI initialization. It drives only `ground_crank` with constant positive rpm. It measures the native solver's slider placement along ground +X and compares that position with an independent analytic reference.

1. Read [the experiment contract](references/experiment.md) for the supported request, sampling, units, and report fields.
2. Call `run_kinematic(request)` from `scripts/kinematic.py`, or invoke the module with a JSON request file in FreeCAD's Python environment. Use a fresh output directory.
3. Deliver `motion.csv`, `motion.svg`, `simulation.FCStd`, `run_config.json`, and the report only when status and checks support the claim. The saved project contains replayable simulation configuration; regenerate frames after reopening.
4. For a real FreeCAD video, call `capture_native_frames` in `scripts/native_video.py` on the successful `simulation.FCStd` inside a FreeCAD GUI process, then call `encode_native_video` in `scripts/encode_video.py` after that GUI process exits. Deliver the MP4 only if both calls succeed. `pixi run demo-native` exercises the full chain on Windows; see [the experiment contract](references/experiment.md).

If the assembly contract is invalid, surface `missing_input` or `failed` without changing its joints. If native FreeCAD simulation is unavailable, return `blocked`. Never substitute the analytical reference for native frame data, or label a reconstruction from `motion.csv` as FreeCAD viewport video.
