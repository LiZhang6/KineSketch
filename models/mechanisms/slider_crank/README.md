# Slider-crank test fixture

`fixture_spec.json` defines the default 30 mm crank radius and 120 mm rod length. `generate_fixture.py` creates four separate editable `.FCStd` parts and `parts_manifest.json` in a **new** directory. The script only uses deterministic Part primitives; no outside model, license claim, generator model, or download is involved.

Run `generate_fixture` inside FreeCAD's Python environment. For example, in its Python console, add the repository root to `sys.path`, then call:

```python
from models.mechanisms.slider_crank.generate_fixture import generate_fixture

generate_fixture(r"C:\path\to\KineSketch\outputs\fixture_001", radius_mm=30, rod_mm=120)
```

Use the returned `parts_manifest.json` as the assembly `manifest_path`. Its paths are relative to the manifest, and each FCStd is bound to its named interfaces by SHA-256. If a part changes, regenerate or explicitly revalidate the interface manifest. The part object's FreeCAD `Name` is used, never its display `Label`.

Local frames: XY mechanism plane; revolute axes +Z; slider guide axes +X (the interface quaternion rotates local Z to global X). Ground crank center is the origin. Rod large end is its local origin and small end is at `(rod_length_mm,0,0)`. Positive slider branch is used. The supported range is zero offset with `rod_length_mm > crank_radius_mm`. A changed dimension generates a new set of parts and matching interface coordinates.

The generated solids are visual test shapes. Pins, holes, bearings, contact and interference behavior are not modeled.
