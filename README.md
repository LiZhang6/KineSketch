# KineSketch

KineSketch is an AI agent integrated into FreeCAD. It can inspect the active
document and perform a small, explicit set of parametric modeling operations from
a dockable chat panel.

## Dependencies

- FreeCAD 1.0 or newer
- An OpenAI-compatible chat-completions endpoint with tool-calling support

No third-party Python package is required inside FreeCAD. The model client uses
Python's standard library.

## Quick Start

1. Install the addon with FreeCAD's Addon Manager or install this repository in
	the Pixi environment.
2. Start FreeCAD and switch to the **KineSketch** workbench.
3. Select **KineSketch > Open Agent** or use the KineSketch toolbar button.
4. Enter an endpoint and model, then ask the agent to create or modify geometry.

For a local Ollama server:

```text
Endpoint: http://127.0.0.1:11434/v1
Model:    qwen2.5:7b
API key:  (empty)
```

The selected model must support OpenAI-style function/tool calling. Cloud and
self-hosted OpenAI-compatible services can be used by changing the endpoint,
model, and API key fields.

These environment variables provide startup defaults:

```powershell
$env:KINESKETCH_ENDPOINT = "https://api.openai.com/v1"
$env:KINESKETCH_MODEL = "gpt-4.1-mini"
$env:KINESKETCH_API_KEY = "..."
```

The endpoint and model are saved in local Qt settings. The API key is retained
only by the current panel and is never written to FreeCAD settings.

## Agent Tools

The initial agent can:

- inspect active document objects and the current selection;
- create parametric boxes and cylinders;
- change an object's position and rotation;
- switch to axonometric view and fit all objects.

Geometry changes use FreeCAD transactions and can be reverted with Undo. The
agent cannot execute arbitrary Python: model requests are limited to the
allowlisted tools in `freecad/KineSketch/agent/tools.py`.

Network requests run outside the GUI thread, while all FreeCAD document changes
run on the GUI thread.

## Build

On Windows, build both the wheel and source distribution with:

```powershell
.\build.ps1
```

The script clears stale artifacts, installs the standard Python `build` frontend
when missing, and writes verified packages to `dist/`. Use `-SkipClean` to retain
existing output or `-NoBootstrap` to prevent automatic dependency installation.

Remove build output, package metadata, Ruff/Pytest caches, and Python bytecode with:

```powershell
.\clean.ps1
```

Both scripts resolve paths from the repository root, so they can be launched from
any working directory. If local PowerShell scripts are disabled, run them without
changing the system policy:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
powershell -ExecutionPolicy Bypass -File .\clean.ps1
```

## Maintainer

me
710559583@qq.com
