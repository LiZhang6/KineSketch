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
