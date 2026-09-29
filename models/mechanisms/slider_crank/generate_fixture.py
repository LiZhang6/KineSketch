"""Compatibility entry point for the packaged slider-crank fixture generator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from freecad.KineSketch.skills.assembly.scripts.fixture import (
    GENERATOR_VERSION, frame, generate_fixture, manifest_template,
)
from freecad.KineSketch.utils.mechanisms.contract import MechanismError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--radius-mm", type=float)
    parser.add_argument("--rod-mm", type=float)
    args = parser.parse_args()
    try:
        response = generate_fixture(args.output_dir, args.radius_mm, args.rod_mm)
    except MechanismError as exc:
        response = {"status": exc.status, "error": {"code": exc.code, "message": str(exc)}}
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
