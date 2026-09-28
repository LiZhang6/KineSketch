# Assembly and kinematic integration for KineSketch

## Scope and repository boundary

`freecad/KineSketch/skills/assembly` (the earlier `skill_2`) turns four FCStd parts with explicit interfaces into a FreeCAD Assembly and verifies its connections. `freecad/KineSketch/skills/kinematic` (the earlier `skill_3`) consumes that exact verified assembly, adds one crank motion, samples native solver placements, and checks them. The first version supports a zero-offset, positive-branch planar slider-crank with `rod_length_mm > crank_radius_mm`. It does not model combustion, forces, contact, flexible bodies, or continuous collision clearance.

The existing frontend template remains in `freecad/KineSketch/`, the addon's namespace package. It maps to the planned `addon_frontend` responsibility. The frontend files and entry points were not changed. The agent, configs, HTML, and MCP directories are structural placeholders with no implementation here.

The upstream `main` commit `7c9f286` moved the planned `agent`, `configs`, `html`, `mcp`, `skills`, and `utils` directories under `freecad/KineSketch/`. This implementation follows that structure: the two named skills live in `freecad/KineSketch/skills` and shared mechanism helpers in `freecad/KineSketch/utils`, within the existing `packages = ["freecad"]` package root. Tests and this documentation remain at the repository root. `models/mechanisms/slider_crank` contains a small deterministic test-part generator; the partner remains responsible for production parts. The new names describe the capability; the old numeric IDs are retained only as a migration note.
The remote commit was fetched for inspection but not merged into this feature branch. Its empty `.gitkeep` files remain on `main` and will be present after the pull request is merged; no duplicate local copies are retained.

## Runtime and resource preparation

Use a FreeCAD Python environment with the built-in Assembly workbench and native simulation methods. System Python in this workspace is 3.12.10 and cannot import `FreeCAD`; the repository-local Pixi environment installs FreeCAD 1.2.0 and its native dependencies under `.pixi/envs/default`. Native simulation must run in FreeCAD's GUI Python process. The code returns `blocked` when the required runtime is unavailable. Do not install a PyPI package named FreeCAD as a substitute for the application runtime.

On Windows, `pixi run test` runs the pure Python tests and skips GUI simulation. `pixi run test-native` starts the installed FreeCAD GUI in an isolated, hidden process and tests assembly and native motion with two generated fixture sizes by default. `pixi run demo-native` runs the fixture-to-video chain and prints the result directory. Set `KINESKETCH_PARTS_MANIFEST` to the partner's `parts_manifest.json` to run either command with external parts instead. Test reports and logs live under ignored `.pixi/native-test-runs/`; successful native tests have no failures or skips. The demo captures real FreeCAD viewport frames, then encodes the MP4 after FreeCAD exits. This checkout also has a local Pixi executable at `.pixi/bin/pixi.exe`, so no system-wide Pixi installation is needed.

## Repeat the full chain on this Windows checkout

From the repository root in PowerShell:

```powershell
.\.pixi\bin\pixi.exe install --locked --platform win-64
.\.pixi\bin\pixi.exe run test
.\.pixi\bin\pixi.exe run test-native
.\.pixi\bin\pixi.exe run demo-native
```

To test the partner's parts instead, set `$env:KINESKETCH_PARTS_MANIFEST = 'C:\path\to\partner\parts_manifest.json'` before `test-native` or `demo-native`. The manifest must reference all four FCStd files. Clear that environment variable to return to generated test fixtures.

The commands check different layers. `test` validates requests, the portable mechanism contract, and analytic checks without FreeCAD GUI; the GUI integration test skips there. By default `test-native` generates two fixture sets, builds each Assembly, reopens the documents, and runs two native simulation cases; a successful run has no failures, errors, or skips. `demo-native` is the visual end-to-end run: it generates four `fixture/*.FCStd` parts and `parts_manifest.json`, builds `assembly/assembly.FCStd` and `assembly/mechanism.json`, creates `motion/simulation.FCStd`, `motion/motion.csv`, `motion/motion.svg` and reports, then captures FreeCAD's axonometric viewport and encodes `motion/freecad_native_3d.mp4`. With an external manifest, the same chain begins from the partner's parts. Each run creates a new ignored `outputs/demo_native_<id>/` directory and prints its paths; `demo_result.json` records every stage's status and either the fixture result or input manifest path. Captured PNGs and their capture report remain in the motion directory, so the video's source can be checked.

The default demonstration uses a 30 mm crank radius and 120 mm rod length; external parts use the dimensions in their manifest. Motion runs at 60 rpm for 2 cycles over 2 s, producing 401 sampled solver positions. FreeCAD 1.2 produces 402 native frames because it duplicates the first frame. The MP4 samples 120 FreeCAD viewport frames at 30 fps, so 2 s of simulated motion plays over 4 s at 0.5× speed. Confirm `assembly_report.json` has `solver_return_code: 0`, `simulation_report.json` has `status: success` and `generation_return_code: 0`, and `freecad_native_3d.report.json` has `status: success`, `source: FreeCAD GUI activeView.saveImage`, `codec: h264`, and `verified_frame_count: 120`. Open the printed MP4 path directly to inspect the actual FreeCAD model motion.

The generator creates four simple test parts and a complete manifest; see the [fixture README](../models/mechanisms/slider_crank/README.md). The partner can instead supply `ground`, `crank`, `connecting_rod`, and `slider` FCStd parts plus one `parts_manifest.json`. The manifest maps each role to its exact FreeCAD object `Name`, relative FCStd path, SHA-256 checksum, and named local interface placements. Its dimensions and interface coordinates must agree with the [assembly contract](../freecad/KineSketch/skills/assembly/references/contract.md). A changed FCStd file needs a new checksum and interface validation before assembly. The assembly skill consumes geometry and does not modify the source parts.

## Python calls

```python
import sys
sys.path.insert(0, r"C:\path\to\KineSketch")
from models.mechanisms.slider_crank.generate_fixture import generate_fixture
from freecad.KineSketch.skills.assembly.scripts.assembly import run_assembly

fixture = generate_fixture(r"C:\path\to\KineSketch\outputs\fixture_001", 30, 120)
manifest_path = fixture["manifest"]
# To use partner parts, set manifest_path to their parts_manifest.json instead.

assembled = run_assembly({
    "template": "slider_crank",
    "manifest_path": manifest_path,
    "fixed_role": "ground",
    "initial_angle_deg": 30,
    "output_dir": r"C:\path\to\KineSketch\outputs\assembly_001",
})
if assembled["status"] == "success":
    from freecad.KineSketch.skills.kinematic.scripts.kinematic import run_kinematic
    simulated = run_kinematic({
        "mechanism_path": assembled["artifacts"]["mechanism"],
        "drive_joint": "ground_crank",
        "rpm": 60,
        "cycles": 2,
        "sample_interval_s": 0.005,
        "initial_angle_deg": 30,
        "observation": {"role": "slider", "point": "wrist_pin", "frame": "ground"},
        "playback_fps": 30,
        "output_dir": r"C:\path\to\KineSketch\outputs\motion_001",
    })
```

Both scripts also accept one JSON request file as a CLI argument when run with FreeCAD's Python; the kinematic script requires the GUI Python process for native simulation. The callable interface is more reliable for direct frontend integration because FreeCAD command-line script handling differs by installation. The experiment may use `duration_s` instead of `cycles`, never both. At 60 rpm, two cycles last 2 s; at 90 rpm they last 4/3 s. `sample_interval_s` is separate from `playback_fps`.

## Contracts and UI handoff

Both calls return `{"status": "success|missing_input|blocked|failed", "artifacts": {...}, "checks": {...}, "warnings": [...], "error": null|{"code": "...", "message": "..."}}`. Every artifact path in a result points to a file that actually exists. A new `output_dir` is required for every run; existing directories are rejected. The frontend should pass selected FCStd parts and their explicit interface manifest as `manifest_path`, rather than infer axes or pins from view selection alone. User rpm, cycles, angle, sampling and playback values map directly to the kinematic request. The agent passes only the successful `mechanism` artifact path from assembly to kinematic.

Show `missing_input` near the affected field; show `blocked` as an environment/capability issue; show `failed` with the report and solver error. Preview the native `assembly.FCStd` or `simulation.FCStd` in FreeCAD. Plot `motion.csv` or display `motion.svg`; for actual FreeCAD video, call `capture_native_frames` and then `encode_native_video` as described in the [kinematic experiment reference](../freecad/KineSketch/skills/kinematic/references/experiment.md). Offer successful CSV, video, reports, configuration and FCStd files as downloads. A source assembly is copied into the motion result package so its `mechanism.json` remains portable. The generated `simulation.FCStd` stores the motion setup but its in-memory frame cache must be regenerated after reopening.

`mechanism.json` schema version 1 records units (mm, s, rad), coordinate convention, assembly-relative file and SHA-256, exact part object names, source checksums, local named interface frames, fixed part, four joint definitions with actual object names, initial branch, single driveable joint, observable point, and assembly validation. The kinematic skill rejects changed or unverified input and never repairs joints. `assembly_report.json` describes solver return code, connected roles, joint residuals, FreeCAD version, and DOF provenance. If the installed FreeCAD Python API does not expose DOF, the report marks it unverified instead of claiming one measured DOF.

`motion.csv` uses measured native solver frames for angle and slider position. Position is the slider wrist point projected onto ground +X from `ground.main_axis`; displacement is relative to the first frame. Speed and acceleration use a local five-sample quadratic fit to positions, with shifted windows at endpoints. `simulation_report.json` records their source, duration, frame count, joint residuals, jumps, theoretical range, and comparison with the independent analytical relation `r cos(theta) + sqrt(l²-r² sin²(theta))` at matching timestamps. The analytical formula is never emitted as native solver data.

## Verification and current limits

Run pure tests with `python -m unittest discover -s tests -p "test_*.py" -v`. The FreeCAD integration test skips outside FreeCAD's GUI Python process; `pixi run test-native` runs it there with generated fixtures by default or partner parts when `KINESKETCH_PARTS_MANIFEST` is set. The Windows Pixi environment completed all ten tests with zero skips under FreeCAD 1.2.0, exercising fixture creation, native assembly, reopening, two simulation profiles, and output extraction. The external-manifest path also passed. `pixi run demo-native` generated and verified a real FreeCAD 3D viewport MP4 with 120 H.264 frames. FreeCAD 1.2 returned one duplicate initial frame; the output records 402 native frames and 401 sampled frames for the 2 s, 0.005 s test. A static import of the unchanged frontend's headless package also passes. The FreeCAD interactive Simulation task panel has shown `Base::Quantity` conversion errors and an over-constraint warning after reopening; the scripted native solver/video path succeeds, but that manual panel issue remains open.

Implementation calls follow the upstream FreeCAD [JointObject](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Assembly/JointObject.py), [Assembly tests](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Assembly/AssemblyTests/TestCore.py), and [simulation command](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Assembly/CommandCreateSimulation.py). Those files document intended APIs; the installed version's behavior is the final check.
