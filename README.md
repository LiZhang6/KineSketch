# KineSketch

KineSketch is an AI agent integrated into FreeCAD. It can inspect the active
document and perform a small, explicit set of parametric modeling operations from
a dockable chat panel.

## Dependencies

- FreeCAD 1.0 or newer
- An OpenAI-compatible chat-completions endpoint with tool-calling support
- A system OpenSSH client available as `ssh`

No third-party Python package is required inside FreeCAD. The model client uses
Python's standard library and the system OpenSSH client.

## Quick Start

1. Configure SSH key authentication and verify passwordless login to the server.
2. Install the addon with FreeCAD's Addon Manager or install this repository in
	the Pixi environment.
3. Start FreeCAD and switch to the **KineSketch** workbench.
4. Select **KineSketch > Open Agent** or use the KineSketch toolbar button.
5. Ask the agent to create or modify geometry.

On Windows with this repository's Pixi environment, start the GUI with
`powershell -ExecutionPolicy Bypass -File .\start_freecad.ps1`. The launcher adds
the environment root to `PATH` so FreeCAD can load `python314.dll`.

For an OpenClaw Gateway running on the same machine:

```text
Endpoint:     http://127.0.0.1:18789/v1
Agent:        openclaw/default
Access token: your OpenClaw Gateway token
```

The checked **SSH key tunnel** section is preconfigured for this deployment:

```text
Server:   61.172.235.130
Port:     6010
Username: asus_gx10
```

With SSH enabled, the endpoint identifies the Gateway from the remote server's
point of view. The default `http://127.0.0.1:18789/v1` is forwarded through a
random loopback port, so the Gateway does not need to be exposed publicly. The
Gateway access token is kept only in the current panel and is never written to
Qt settings or logs.

KineSketch starts OpenSSH with batch mode, password authentication disabled, and
strict host-key checking. It never reads a server password, private key, or key
passphrase. Before opening the Agent panel, generate an RSA key and install its
public key on the server:

```bash
ssh-keygen -t rsa
ssh-copy-id -i ~/.ssh/id_rsa.pub -p 6010 asus_gx10@61.172.235.130
```

Enter the server password once when `ssh-copy-id` requests it. It also records
the server in `known_hosts`. Then verify that this command succeeds without a
password prompt:

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p 6010 asus_gx10@61.172.235.130 exit
```

The OpenClaw Chat Completions endpoint must be enabled on the Gateway:

```json5
{
	gateway: {
		http: {
			endpoints: {
				chatCompletions: { enabled: true }
			}
		}
	}
}
```

For a remote deployment without the built-in SSH tunnel, uncheck **SSH key tunnel**
and use an HTTPS endpoint such as `https://agent.example.com/v1`. Keep the
Gateway behind private ingress or an authenticated reverse proxy; its bearer
token grants operator-level access.
`openclaw/default` selects the default remote Agent, while
`openclaw/<agentId>` selects a specific configured Agent. The underlying AI
model and provider credentials remain entirely in the remote OpenClaw environment.

These environment variables provide startup defaults:

```powershell
$env:KINESKETCH_AGENT_ENDPOINT = "http://127.0.0.1:18789/v1"
$env:KINESKETCH_AGENT_ID = "openclaw/default"
$env:KINESKETCH_AGENT_TOKEN = "..."
$env:KINESKETCH_SSH_HOST = "61.172.235.130"
$env:KINESKETCH_SSH_PORT = "6010"
$env:KINESKETCH_SSH_USER = "asus_gx10"
```

The endpoint and Agent ID are saved in local Qt settings. The access token is
retained only by the current panel and is never written to FreeCAD settings.
The older `KINESKETCH_ENDPOINT`, `KINESKETCH_MODEL`, and `KINESKETCH_API_KEY`
environment variables remain supported as fallbacks.

Each chat panel creates an application-owned conversation ID and sends it as the
OpenAI `user` field. OpenClaw therefore retains one remote session until the user
presses **Clear**, which starts a new conversation.

The transport boundary is `AgentClient` in
`freecad/KineSketch/agent/client.py`. Its factory selects either direct HTTP or
the system-OpenSSH tunnel while keeping the UI independent from the transport.

## Agent Tools

The initial agent can:

- inspect active document objects and the current selection;
- create parametric boxes and cylinders;
- change an object's position and rotation;
- switch to axonometric view and fit all objects;
- capture the current FreeCAD 3D viewport and send the screenshot as an image to
  a vision-capable model;
- turn a text description of the supported four-part, rail-guided slider-crank
  into a new box/cylinder feature plan and four separate FreeCAD solids, verify its
  native FreeCAD Assembly and motion, export a FreeCAD viewport MP4, and replay
  its motion in the GUI.

Geometry changes use FreeCAD transactions and can be reverted with Undo. The
agent cannot execute arbitrary Python: model requests are limited to the
allowlisted tools in `freecad/KineSketch/agent/tools.py`.

Network requests run outside the GUI thread, while all FreeCAD document changes
run on the GUI thread.

### Experimental visual check

Select **Visual check after actions (experimental)** in the Agent panel to send
one viewport screenshot to the configured model after a successful action turn.
The model receives a PNG as an OpenAI-compatible `image_url` data URI and returns
a visual review without access to CAD tools in that review request. The checkbox
is off by default and its state is saved in local Qt settings. You can also ask
the agent to call `capture_viewport` when you want a screenshot inspected during
a conversation. The screenshot is captured from the currently active 3D view at
1024 × 768 pixels. The tool result and panel transcript show only image metadata;
the temporary PNG is removed after encoding.

Use a model and endpoint that accept image input. Qwen3.6 supports Base64 image
input through the OpenAI-compatible chat API, but a particular local model build
or inference server still needs to expose its vision capability. If image input
is rejected, the panel reports the endpoint error. The check is visual evidence
only: hidden geometry, exact measurements, and solver state still require the
structured FreeCAD and Assembly results.

`build_slider_crank_from_plan` and `replay_slider_crank` are the chat tools for
the documented slider-crank example. The model supplies explicit dimensions
and local coordinates for additive features; the geometry skill builds fresh
FCStd parts without loading the fixed demo fixture. The Assembly and Kinematic
skills then build four native joints, verify solver motion, and can save an
H.264 MP4 captured from the actual FreeCAD viewport. The supported topology is
one zero-offset slider-crank, not arbitrary assemblies or simulation of
unrelated primitives. The old `create_slider_crank_demo` handler remains for
existing local callers but is not offered to the chat model.
See [the repeatable Chinese GUI prompt](docs/slider_crank_gui_demo_prompt_zh.md)
and [assembly and kinematic integration](docs/zhang_assembly_kinematic.md).

## Diagnosing chat tool calls

A text response verifies connectivity, not the ability to call FreeCAD tools.
The server must return structured `tool_calls` for the functions supplied in the
request. Replies such as "searching for FreeCAD tools" do not execute any CAD action.
The panel displays each local tool result and labels text-only responses as having
no FreeCAD actions.

An SSH `Permission denied (publickey,password)` error occurs before contacting the
model. Verify the passwordless login command above using the same Windows account
and OpenSSH installation that launches FreeCAD.

If the Gateway returns text but no client tools, test the underlying model's
OpenAI-compatible endpoint directly through the same SSH tunnel. For a remote
Ollama server, use `http://127.0.0.1:11434/v1` as Endpoint and the exact installed
Ollama model name as Agent, with SSH still enabled. The address refers to the remote
server. This mode talks directly to the model, so `openclaw/default` is not a model
name for this endpoint. Configure the access token as required by that server.

Run `pixi run test-native` to test the actual FreeCAD dock with deterministic
local replies and a separate streaming-response parser check, as well as native
assembly and motion. The dock test executes the box/cylinder/placement/view
sequence across multiple reply rounds and forces GUI event re-entry while applying
tools. It does not contact a model or
modify the user's Agent connection settings. Set `KINESKETCH_AGENT_ARTIFACT_DIR`
to a new output directory to retain its FCStd model, viewport PNG, and JSON report.
This local test must be distinguished from a live model/SSH test.

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
