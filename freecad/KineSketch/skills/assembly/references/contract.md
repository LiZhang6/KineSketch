# Assembly request and part contract

The Python entry point is `freecad.KineSketch.skills.assembly.scripts.assembly.run_assembly(request)`. CLI: `python -m freecad.KineSketch.skills.assembly.scripts.assembly request.json` inside FreeCAD's Python environment with the repository root on `PYTHONPATH`.

```json
{
  "template": "slider_crank",
  "manifest_path": "C:/project/partner_parts/parts_manifest.json",
  "fixed_role": "ground",
  "initial_angle_deg": 30,
  "output_dir": "C:/project/outputs/assembly_001"
}
```

The manifest is schema version 1 with `mechanism_type=slider_crank`, `units={"length":"mm","angle":"rad"}`, `parameters={"crank_radius_mm":30,"rod_length_mm":120,"offset_mm":0}`, and exact roles `ground`, `crank`, `connecting_rod`, `slider`. Each role has `file` (relative `.FCStd` path), `object_name` (FreeCAD `Name`), `sha256`, and `interfaces`. Each interface is `{ "origin_mm": [x,y,z], "rotation_xyzw": [x,y,z,w] }`. The [test fixture generator](../../../../../models/mechanisms/slider_crank/README.md) emits a complete manifest; partner parts may provide one too. Editing a source FCStd invalidates its checksum; revalidate its interfaces before assembly.

The supported mechanism lies in the XY plane with revolute axes along local +Z. The ground's `main_axis` and `guide_axis` origins are `(0,0,0)`; its guide quaternion is `(0, sqrt(1/2), 0, sqrt(1/2))`, rotating local +Z onto ground +X. The crank's `main_axis` is `(0,0,0)` and `crank_pin` is `(r,0,0)`. The connecting rod's `rod_big_end` is `(0,0,0)` and `rod_small_end` is `(l,0,0)`. The slider's `wrist_pin` and `guide_axis` origins are `(0,0,0)`; its guide uses the same quaternion. Other interface quaternions are `(0,0,0,1)`. This first version requires zero offset, positive slider branch, and `l > r`.

The result package contains `assembly.FCStd` with copied shapes and native joints, `mechanism.json` with relative `assembly_file`, and `assembly_report.json`. Source FCStd files are not changed or needed to reopen the assembly. The FreeCAD solver must return success and joint residuals must pass; native DOF is reported only if the installed API exposes it.
