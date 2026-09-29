---
name: kinesketch-geometry
description: Build fresh FreeCAD solids from a model-authored, bounded feature plan for the supported four-part slider-crank.
---

# Geometry

Use this skill when a text description must determine the actual solids instead of selecting the deterministic demo fixture. The model must supply a feature plan for `ground`, `crank`, `connecting_rod`, and `slider`. Each part contains one or more additive `box` or `cylinder` features with explicit millimetre dimensions and local coordinates. No existing FCStd part is loaded.

The local coordinate contract matches the [assembly skill](../assembly/SKILL.md): ground pivot and guide interfaces are at the origin, crank interfaces at local X=0 and X=crank radius, rod interfaces at local X=0 and X=rod length, and slider pin/guide interfaces at its origin. The planner must put material around these points. The ground plan must describe a base, two guide rails and a pivot. The crank and rod plans must each describe a connecting bar and two end bosses. The slider must be a solid body that fits between the rails.

Call `generate_parts_from_plan(output_dir, radius_mm, rod_mm, parts_plan)` in `scripts/parts.py` inside FreeCAD's Python runtime. It validates the plan, builds four new single-solid FCStd files, and writes `design_plan.json` and a checksum-bound `parts_manifest.json`. Do not substitute `generate_fixture` or copy existing models. Reject a plan if a role is missing, solids are invalid or disconnected, or a required joint interface lacks material.

Only pass a successful manifest to the assembly skill. Its current solver contract supports zero-offset slider-crank geometry; this skill does not make arbitrary mechanism topologies supported.
