# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stdio MCP server, launched with a Python runtime that can import FreeCAD."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import Any

from ..skills import modeling_instructions


def create_server():
    from mcp.server.lowlevel import Server
    from mcp.types import CallToolResult, GetPromptResult, Prompt, PromptMessage, TextContent, Tool

    # FreeCAD startup messages must not enter the stdio JSON-RPC transport.
    with contextlib.redirect_stdout(sys.stderr):
        from ..agent.tools import TOOL_DEFINITIONS, execute_tool_call

    server = Server("kinesketch")
    definitions = [item["function"] for item in TOOL_DEFINITIONS]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=item["name"], description=item["description"],
                 inputSchema=item["parameters"])
            for item in definitions
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        # Synchronous execution stays on this process's event-loop thread.
        with contextlib.redirect_stdout(sys.stderr):
            result = json.loads(execute_tool_call({"function": {
                "name": name, "arguments": json.dumps(arguments),
            }}))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            isError=not result.get("ok", False),
        )

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [Prompt(name="generate-3d-model", description="Natural-language FreeCAD modeling")]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
        if name != "generate-3d-model":
            raise ValueError(f"Unknown prompt: {name}")
        return GetPromptResult(messages=[
            PromptMessage(role="user", content=TextContent(
                type="text", text=modeling_instructions(),
            )),
        ])

    return server


async def run() -> None:
    from mcp.server.stdio import stdio_server

    server = create_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
