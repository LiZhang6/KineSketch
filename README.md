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

The default configuration connects to the model service on the remote server:

```text
Endpoint:     http://127.0.0.1:11434/v1
Agent:        modelscope.cn/unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M
Access token: leave empty for this deployment
```

The checked **SSH key tunnel** section is preconfigured for this deployment:

```text
Server:   61.172.235.130
Port:     6010
Username: asus_gx10
```

With SSH enabled, the endpoint identifies the service from the remote server's
point of view. The default `http://127.0.0.1:11434/v1` is forwarded through a
random loopback port, so the service does not need to be exposed publicly. The
optional access token is kept only in the current panel and is never written to
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

OpenClaw is optional, not required by the default deployment. To use an OpenClaw
Gateway instead, enter its actual endpoint (commonly
`http://127.0.0.1:18789/v1`), Agent ID and access token, and enable its Chat
Completions endpoint:

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
service behind private ingress or an authenticated reverse proxy.
For a direct model service, the Agent field contains the exact model ID returned
by `/v1/models`. For OpenClaw only, `openclaw/default` selects the default remote
Agent and `openclaw/<agentId>` selects a specific configured Agent.

These environment variables provide startup defaults:

```powershell
$env:KINESKETCH_AGENT_ENDPOINT = "http://127.0.0.1:11434/v1"
$env:KINESKETCH_AGENT_ID = "modelscope.cn/unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
$env:KINESKETCH_AGENT_TOKEN = ""
$env:KINESKETCH_SSH_HOST = "61.172.235.130"
$env:KINESKETCH_SSH_PORT = "6010"
$env:KINESKETCH_SSH_USER = "asus_gx10"
```

The endpoint and Agent ID are saved in local Qt settings. The access token is
retained only by the current panel and is never written to FreeCAD settings.
The older `KINESKETCH_ENDPOINT`, `KINESKETCH_MODEL`, and `KINESKETCH_API_KEY`
environment variables remain supported as fallbacks.
The retired saved default pair (`18789/v1`, `openclaw/default`) is replaced by
these startup defaults when opening the panel; other saved configurations are
preserved. To intentionally reuse that old pair, supply it via the environment.

Each chat panel creates an application-owned conversation ID and sends it as the
OpenAI `user` field. OpenClaw can use it to retain a remote session until the user
presses **Clear**. A direct model service may ignore this ID; it does not imply
server-side conversation memory. The current client sends the current turn and
its tool rounds, not earlier turns.

For an explicit creation request, the panel requires a tool call on the first
round. A crank creation request offers `create_crank` first, with optional
arguments and feasible defaults. Informational and how-to questions do not
force document edits. After executing tools, normal tool selection resumes for
view fitting, saving and the final reply. If the model returns only text instead
of an action, the panel asks once more, then reports a tool-calling error; it
does not silently create a part locally or retry interrupted network requests.

The transport boundary is `AgentClient` in
`freecad/KineSketch/agent/client.py`. Its factory selects either direct HTTP or
the system-OpenSSH tunnel while keeping the UI independent from the transport.

## Agent Tools

### Image Input

Use **Attach image** in the agent panel to select one PNG, JPEG or WebP file
(up to 10 MiB), optionally add text, then press **Send**. The filename appears
beside the attachment button; **Remove image** discards it before sending.
Images are sent as base64 `image_url` content alongside the text to your
configured remote endpoint, including through the SSH tunnel. Only attach
images you intend to upload. Attachment data is not saved to Qt settings.

The remote agent and its underlying model must support vision and accept
multimodal Chat Completions messages with data URLs. KineSketch does not add
a local vision model or silently retry as text-only if the backend rejects
images. Clear starts a new remote conversation and clears local attachments
and history; it does not delete data already retained by the remote service.

For a crank drawing, include readable dimensions and units. A photo without
scale cannot establish precise sizes. Missing dimensions and density use
explicit prototype assumptions instead of blocking creation; these are not
measurements or verified material properties. Conflicting explicit values
still require clarification.
External MCP clients can likewise attach images to their vision model and pass
the extracted known dimensions to `create_crank`.

The bundled `generate-3d-model` skill is loaded automatically into the agent's
system prompt. Describe a part, for example: "Create a 60 x 40 x 8 mm mounting
plate with a centred 10 mm through-hole." The agent translates the description
into a structured `generate_model` plan and creates an editable parametric tree.
Plans support boxes, cylinders, spheres, unions and cuts, including primitive
position and rotation. Every plan is one transaction; invalid geometry rolls
back all of its operations. Sketches, fillets and lofts are not supported yet.

Generated models use native FreeCAD Part objects. The default saved format is
`.FCStd`, retaining editable dimensions, placements and boolean dependencies.
For example, ask: "Create the mounting plate and save it as
E:/models/mounting_plate.FCStd." The output directory must already exist.
`save_model` automatically appends `.FCStd` when a path has no extension.
STEP and STL are explicit exports and do not preserve FreeCAD parametric history.

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

Streamed text is batched in the worker and refreshed by the panel at 75 ms
intervals, with a final flush when a response ends. Tool calls run one at a time
through the Qt event loop. The panel stays busy until both the network thread
and the tool queue finish; switching active documents skips remaining actions
instead of writing into the wrong document. Successful creation shows the tool
result and elapsed time immediately and fits the view locally, before the next
model request. View-fitting failure does not undo creation or prevent saving.

Crank recompute reads the inertia matrix once and creation reuses its physics
result. These changes reduce redundant work and UI updates, but a single native
FreeCAD geometry operation or document recompute can still block the GUI while
it runs. Native responsiveness and timing require testing in FreeCAD.

Use **Stop** to end the current turn immediately, interrupt its HTTP connection,
and discard queued tools and late responses. You can send a new message without
waiting for background connection cleanup. Completed CAD changes are preserved;
a native geometry operation already running on the GUI thread cannot be safely
interrupted. Disconnecting does not guarantee the server stops inference. The
button is disabled when idle; there is no resume action.

## Modeling Skill and MCP

### Cranks and Physical Parameters

The embedded agent and MCP expose `create_crank` for a rounded two-hole crank
arm. Supply the centre distance (crank radius), arm width, thickness, shaft-hole
diameter and pin-hole diameter in mm, plus a uniform density in kg/m^3 and its
source. All inputs are optional: missing parameters use feasible prototype
defaults. With no dimensions, the defaults are 100 mm centre distance, 30 mm
width, 8 mm thickness, and 12/8 mm holes. Partial dimensions adjust missing
values proportionally with hole-clearance checks; explicit inputs are never
overwritten. Missing density uses 7850 kg/m^3 as an unverified engineering
assumption, not a material lookup. A supplied density without a source is
marked as unverified user input. Invalid explicit values still fail validation.
Tool results include resolved `parameters` and `modeling_assumptions`; the
FreeCAD object's `ModelingAssumptions` stores the initial creation assumptions,
not a live edit history. Replace assumed dimensions and density with verified
values before using the mass and inertia for engineering decisions.

Example request: "Create a crank with centre distance 100 mm, width 30 mm,
thickness 8 mm, shaft hole 12 mm and pin hole 8 mm. Use my supplied density of
7850 kg/m^3; record its source as my stated engineering assumption. Save it as
E:/models/crank.FCStd." This is an example assumption, not certified material data.

The native `Part::FeaturePython` stores editable geometry and density, computed
mass, local centre of mass, centroidal inertia tensor and shaft-axis inertia.
Computed values carry units and are read-only in the property editor. Editing
dimensions or density and recomputing updates both shape and physics. Keep
KineSketch installed when reopening the file so FreeCAD can restore its proxy.

The model assumes a uniform rigid solid; shaft, pin and bearing masses are not
included. The shaft is the local Z axis through (0, 0), with the pin centre on
positive X. This provides physical parameters for future dynamics integration;
it does not yet simulate crank motion, stresses or bearing friction. Inertia uses
FreeCAD/OCC volume integrals about the centre of mass and the parallel-axis
theorem for the shaft ([OCC mass-properties reference](https://occt3d.com/dev/doc/refman/html/class_g_prop___g_props.html)).

The skill lives in `freecad/KineSketch/skills/generate-3d-model/SKILL.md`.
Its `agents/openai.yaml` binds it to the `kinesketch` stdio MCP server. The server
exposes the same tool schemas and handlers as the embedded agent, including
`save_model` for FCStd, STEP and STL files. It also exposes the skill as the
`generate-3d-model` MCP prompt. The LLM interprets natural language; the MCP tools
execute validated modeling plans, not arbitrary Python.

The MCP server runs in a separate process with its own FreeCAD document. It does
not control an already-open FreeCAD GUI. Ask the MCP client to save the result
with `save_model` and open that file in FreeCAD. Existing output files are not
overwritten. The remote SSH Agent Gateway does not need the MCP SDK; the MCP
server belongs on the machine where FreeCAD performs the modeling.

Use a Python environment compatible with the installed FreeCAD build, where
`import FreeCAD` and `import Part` both succeed. Install this project and the
optional MCP dependency in that environment:

```bash
python -c "import FreeCAD, Part"
python -m pip install -e ".[mcp]"
python -m freecad.KineSketch.mcp.server
```

`freecad/KineSketch/configs/mcp.json` is a client configuration example. Set its
`command` to the absolute path of that Python executable before adding the
`kinesketch` entry to your MCP client's configuration. Install or copy the skill
directory into that client's skill discovery directory. Binding metadata alone
does not install or register an MCP server in an external client.

The embedded panel uses the same modeling functions directly, so it needs no
MCP SDK installation. The standalone server uses the
[official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x),
installed through the optional extra above.

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
