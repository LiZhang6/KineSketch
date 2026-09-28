# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic skills and bundled instructions for the agent and MCP server."""

from pathlib import Path


def modeling_instructions() -> str:
    source = Path(__file__).parent / "generate-3d-model" / "SKILL.md"
    return source.read_text(encoding="utf-8").split("---", 2)[2].strip()
