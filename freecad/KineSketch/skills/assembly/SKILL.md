---
name: kinesketch-assembly
description: Build a verified four-part slider-crank Assembly in FreeCAD from checksum-bound FCStd parts with named interfaces; use when KineSketch needs its assembly stage.
---

# Assembly

Use this skill for the first-version zero-offset slider-crank mechanism. It requires four independent `.FCStd` parts, a `parts_manifest.json` with named local interface frames and checksums, and a FreeCAD Python runtime with the built-in Assembly workbench. It does not infer interfaces from anonymous mesh faces.

1. Obtain the partner's four FCStd parts and `parts_manifest.json`, or call `models/mechanisms/slider_crank/generate_fixture.py` to create deterministic test parts. Read [the input contract](references/contract.md) for dimensions, local frames, and manifest fields.
2. Pass a JSON request to `run_assembly(request)` from `scripts/assembly.py`, or invoke that module with a request JSON file. The script validates inputs, creates a new output directory, copies geometry into a new Assembly, fixes ground, creates four native joints, solves, checks residuals, and writes its structured result.
3. Only pass `mechanism.json` onward when status is `success` and `assembly_validation.status` is `verified`. Read [the integration guide](../../../../docs/zhang_assembly_kinematic.md) when connecting a frontend or agent.

On `missing_input`, request corrected resources or parameters. On `blocked`, show the FreeCAD capability error. On `failed`, show the report and retain the diagnostic run directory. Do not claim assembly success from manually positioned parts.
