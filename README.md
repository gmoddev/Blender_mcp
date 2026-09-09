> **Inspired by [blender-mcp](https://github.com/ahujasid/blender-mcp) by Siddharth Ahuja** — the original proof-of-concept that demonstrated connecting AI agents to Blender over MCP. This fork retains 69 tool groups and 550+ actions while hardening the system toward a production-grade architecture. The current unit suite contains 535 tests.

---

# Blender MCP

**Control Blender with AI through an explicit, testable local trust boundary.**

Give Claude, GPT, or any MCP-capable AI the ability to create, inspect, and animate 3D scenes in Blender using plain language and structured tool calls.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Blender 5.0+](https://img.shields.io/badge/Blender-5.0%2B-E87D0D?logo=blender&logoColor=white)](https://www.blender.org/)
[![MCP](https://img.shields.io/badge/MCP-JSON--RPC%202.0-6C47FF)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What is this?

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/) is an open standard that lets AI assistants call structured tools — the same way a programmer calls an API. This project implements an MCP server that bridges your AI assistant to a live Blender session running on your computer.

In practice: tell your AI *"create a red metallic sphere above the cube"* or *"check if all drone parts are touching"* and it will call the right Blender tools, get real geometry data back, and show you a screenshot — no Python required from your side.

> **Authenticated local control.** Blender must be installed and running locally. Loopback is not treated as caller identity: the add-on and bridge require the same authentication credential before tool discovery or execution.

> **Raw Python is a separate high-risk capability.** `execute_blender_code`, text-block execution, and the legacy alias run unrestricted Python with the Blender user's authority. They are denied in Safe Mode and remain denied in Full Structured Mode until **Allow Raw Python** is explicitly enabled.

> **Asset safety status.** Foundation 0 is not complete. The scene/export family now uses explicit local read/write roots, but remaining file surfaces, external integrations, provider-secret migration, atomic publication, and live cross-platform validation remain open. Continue using disposable `.blend` copies.

---

## Fork Direction

This fork is hardening the command lifecycle and local trust boundary before expanding the character-authoring tool surface. Start with the [roadmap](ROADMAP.md), [architecture assessment](docs/ARCHITECTURE.md), [security invariant registry](docs/SECURITY_INVARIANTS.md), [protocol contract](docs/PROTOCOL.md), and [retained security scan](docs/security/SECURITY_SCAN_2026-09-09.md). Architectural decisions are recorded in [`docs/adr/`](docs/adr/).

Until the command lifecycle and authorization milestones are complete, use disposable copies of valuable `.blend` files for agent-driven mutations.

---

## Architecture

<details>
<summary><strong>System diagram</strong></summary>

```
Claude / AI Agent
       │  stdio  (JSON-RPC 2.0 / MCP)
       ▼
stdio_bridge.py              ← MCP bridge  [standard Python, runs outside Blender]
       │                       · Validates JSON schemas before forwarding
       │                       · Caches tool list from Blender on first connect
       │  authenticated TCP localhost:9879
       │  protocol-v1 envelope + bounded length-prefix JSON
       ▼
Blender Addon                ← blender_mcp/__init__.py  [runs inside Blender]
  ├── dispatcher.py            Command router + handler registry (HANDLER_REGISTRY)
  ├── handlers/                52 handler modules (manage_*.py)
  │     manage_scene_comprehension.py   11-action scene intelligence suite
  │     manage_rendering.py             Render, screenshot, view control
  │     manage_scripting.py             unrestricted Python (separately gated)
  │     polyhaven / sketchfab / hunyuan / hyper3d  (4 external integrations)
  └── core/
        protocol.py           Wire protocol (4-byte header + JSON)
        thread_safety.py      All bpy calls routed to Blender's main thread
        execution_engine.py   Safe bpy.ops wrapper + ExecutionPolicy
        parameter_validator.py Type coercion + JSON schema validation
        intent_router.py      Multi-language intent classification (EN/TR/FR)
        semantic_memory.py    Tag-based object resolution
        security.py           capability policy / Safe Mode
        job_manager.py        Async subprocess + internal job queue
```

All `bpy` operations are marshalled to Blender's main thread via `bpy.app.timers`, preventing `EXCEPTION_ACCESS_VIOLATION` crashes that would occur if a background TCP thread touched the Blender API directly.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed diagrams.

</details>

---

## Installation

<details open>
<summary><strong>Prerequisites</strong></summary>

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Platform** | Windows validated | macOS/Linux are design targets but require CI and live Blender validation |
| [Blender](https://www.blender.org/download/) | **5.0 or later** | Must be installed and running locally |
| [Python](https://www.python.org/downloads/) | **3.10 or later** | For the MCP bridge (outside Blender) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | latest | Recommended — fast, isolated environments |
| [git](https://git-scm.com/) | any | To clone the repository |

</details>

### Step 1 — Clone the repository

```bash
git clone https://github.com/gmoddev/Blender_mcp.git
cd Blender_mcp
```

### Step 2 — Set up the Python environment

<details>
<summary>Using <strong>uv</strong> (recommended)</summary>

```bash
# Create .venv and install all dependencies in one command
uv sync --all-extras

# Verify everything works (no Blender needed for tests)
uv run pytest tests/unit -q
# → 535 passed in ~1.6s
```

`uv sync` creates `.venv/` in the project directory — your system Python stays clean.
All `uv run <cmd>` calls use this environment automatically, no manual activation needed.

</details>

<details>
<summary>Using <strong>pip</strong> (alternative)</summary>

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -e ".[dev]"
```

</details>

### Step 3 — Build the Blender addon ZIP

```bash
python create_release_zip.py
# → blender_mcp_v1.0.0.zip
```

### Step 4 — Install the addon in Blender

```
Blender → Edit → Preferences → Add-ons → Install
  → select blender_mcp_v1.0.0.zip
  → enable "Blender MCP"
```

In Blender Addon Preferences, use **Generate / Rotate Credential** to create the required 256-bit
base64url token. Copy that exact value to `BLENDER_MCP_AUTH_TOKEN` in the MCP client configuration.
Do not invent a password: manually chosen tokens are rejected. Then press **N** in the 3D Viewport
→ **MCP** → **Connect to MCP server**. The server refuses to start without a valid credential and
binds to loopback only. Addon Preferences are authoritative for the Blender server; the environment
variable is only its fallback, so a rotated preference cannot silently revert after restart.

---

## MCP Client Configuration

Replace `<path-to-blender-mcp>` with the absolute path where you cloned this repo.

<details open>
<summary><strong>Claude Desktop</strong> — <code>claude_desktop_config.json</code></summary>

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "blender": {
      "command": "python",
      "args": ["-u", "<path-to-blender-mcp>/stdio_bridge.py"],
      "env": {
        "BLENDER_HOST": "localhost",
        "BLENDER_PORT": "9879",
        "BLENDER_MCP_AUTH_TOKEN": "<same-credential-as-Blender-preferences>",
        "MCP_TRANSPORT": "stdio",
        "PYTHONPATH": "<path-to-blender-mcp>"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Cursor / Windsurf / VS Code</strong> — <code>.cursor/mcp.json</code> or <code>.vscode/mcp.json</code></summary>

```json
{
  "mcpServers": {
    "blender": {
      "command": "python",
      "args": ["-u", "<path-to-blender-mcp>/stdio_bridge.py"],
      "env": {
        "BLENDER_HOST": "localhost",
        "BLENDER_PORT": "9879",
        "BLENDER_MCP_AUTH_TOKEN": "<same-credential-as-Blender-preferences>",
        "MCP_TRANSPORT": "stdio",
        "PYTHONPATH": "<path-to-blender-mcp>"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>OpenAI Codex CLI</strong> — <code>~/.codex/config.toml</code></summary>

```toml
[mcp_servers.blender]
command = "python"
args = ["-u", "<path-to-blender-mcp>/stdio_bridge.py"]
cwd = "<path-to-blender-mcp>"
enabled = true

[mcp_servers.blender.env]
BLENDER_HOST = "localhost"
BLENDER_PORT = "9879"
BLENDER_MCP_AUTH_TOKEN = "<same-credential-as-Blender-preferences>"
MCP_TRANSPORT = "stdio"
PYTHONPATH = "<path-to-blender-mcp>"
```

</details>

<details>
<summary><strong>Generic shell / .env</strong></summary>

```bash
export BLENDER_HOST=localhost
export BLENDER_PORT=9879
export MCP_TRANSPORT=stdio
export PYTHONPATH=<path-to-blender-mcp>

python -u <path-to-blender-mcp>/stdio_bridge.py
```

</details>

> **`-u` flag** — disables Python's stdout/stderr buffering. Required for stdio transport. Without it, MCP messages may be held in Python's internal buffer and never reach the client.

<details>
<summary>Environment variables reference</summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `BLENDER_HOST` | `localhost` | Hostname where Blender's addon is listening |
| `BLENDER_PORT` | `9879` | TCP port of the Blender addon |
| `MCP_TRANSPORT` | `stdio` | Transport mode (`stdio` for all current clients) |
| `PYTHONPATH` | — | Must include the project root so `blender_mcp` is importable |

</details>

---

## Verify the Connection

```
Tool: get_server_status
→ {
    "status": "active",
    "blender_version": [5, 0, 0],
    "blender_language": "en_US",
    "handler_count": 69,
    "next_step": "Call list_all_tools to see all available tools…"
  }
```

---

## Recommended First Workflow

```
1. manage_agent_context  GET_PRIMER       → full quick-start guide for the AI
2. get_scene_graph       GET_OBJECTS_FLAT → what objects exist + world positions
3. get_viewport_screenshot_base64         → see the current viewport
4. execute_blender_code                   → create or modify objects
5. get_viewport_screenshot_base64         → confirm the result visually
```

---

## ESSENTIAL Tools (priority 1–9)

<details open>
<summary>These 9 tools handle the vast majority of tasks. The AI discovers them first.</summary>

| Pri | Tool | Purpose |
|-----|------|---------|
| 1 | [`execute_blender_code`](#-execute_blender_code) | Unrestricted `bpy` Python — denied unless Raw Code Mode is explicitly enabled |
| 2 | [`get_scene_graph`](#-get_scene_graph) | 11-action scene intelligence suite |
| 3 | [`get_viewport_screenshot_base64`](#-get_viewport_screenshot_base64) | Visual verification — see what Blender sees |
| 4 | `get_object_info` | Deep object inspector — modifiers, constraints, animation |
| 5 | `get_local_transforms` | Parent-relative coordinates |
| 6 | `manage_agent_context` | Workflow guides (`GET_PRIMER`, `GET_TACTICS`) |
| 7 | `list_all_tools` | Tool discovery with intent filtering (77% token savings) |
| 8 | `get_server_status` | Health check — version, language, handler count |
| 9 | `new_scene` | Create an empty scene |

</details>

### ⚡ execute_blender_code

The most powerful and highest-risk tool. It executes arbitrary Python with full `bpy`, Python
builtins, filesystem, process, and network authority inside Blender. Safe Mode and Full Structured
Mode deny it; use only after explicitly enabling Raw Code Mode.

> **Not a sandbox:** the render-expression guard below addresses one availability bug only. It does
> not restrict what Python can do. Prefer audited structured actions whenever possible.

**One hardcoded guard:** `bpy.ops.render.render()` is always blocked — it freezes Blender's main thread for the entire render duration, making the MCP unresponsive. Use `manage_rendering action=RENDER_FRAME` instead (async subprocess).

```python
# Create a metallic sphere
import bpy, bmesh
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0, 1))
obj = bpy.context.active_object
mat = bpy.data.materials.new("Metal")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Metallic"].default_value = 1.0
bsdf.inputs["Roughness"].default_value = 0.1
obj.data.materials.append(mat)
print(f"Created: {obj.name}")
```

### 🔍 get_scene_graph

11-action scene intelligence suite. The AI uses this to understand what's in the scene before modifying it.

<details>
<summary>All 11 actions</summary>

| Action | Description |
|--------|-------------|
| `GET_OBJECTS_FLAT` | All objects: world transforms, hierarchy, `matrix_world`, `geometry_center_world` |
| `GET_SCENE_MATRIX` | Deep spatial analysis: AABB center, nearest neighbors, distances |
| `ANALYZE_ASSEMBLY` | Integrity score 0–100: BVH surface gaps, interpenetration, hierarchy-aware pairs |
| `CHECK_INTERSECTION` | AABB intersection test between two objects |
| `GET_SPATIAL_REPORT` | Spatial summary for one object: bounds, nearby objects |
| `CAST_RAY` | Ray cast from `origin=[x,y,z]` in `direction=[dx,dy,dz]` |
| `VERIFY_ASSEMBLY` | Rule-based checks: `must_touch`, `parent_must_be` |
| `DETECT_GEOMETRY_ERRORS` | Per-object: non_manifold_edges, boundary_edges, zero_area_faces |
| `GEOMETRY_COMPLEXITY` | Triangle/vertex/ngon counts + complexity tier |
| `CHECK_PRODUCTION_READINESS` | Manifold, UV, materials, naming, origin alignment; score 0–100 |
| `GET_HIERARCHY_TREE` | BFS parent→children tree up to `max_depth` |

</details>

### 📸 get_viewport_screenshot_base64

Captures the viewport as a base64 PNG the AI can directly see and reason about.

<details>
<summary>Key parameters</summary>

| Parameter | Example | Description |
|-----------|---------|-------------|
| `view_direction` | `"MULTI"` | `FRONT`, `RIGHT`, `TOP`, `ISOMETRIC`, or `MULTI` (all four) |
| `target_objects` | `["Arm_L", "Body"]` | Frame specific objects instead of full scene |
| `gap_focus_m` | `0.002` | Auto-zoom to show a gap of this distance |
| `display_mode` | `"MATERIAL"` | `SOLID`, `MATERIAL`, or `WIREFRAME` |
| `action` | `"SMART_SCREENSHOT"` | Automatically picks the best view |

</details>

---

## Tool Tier System

The AI context window would overflow if all 69 tools were listed in full detail every time. The tier system solves this:

| Tier | Priority | Count | Format in `list_all_tools` |
|------|----------|-------|---------------------------|
| **ESSENTIAL** | 1–9 | 9 | Full detail — title, description, all parameters |
| **CORE** | 10–49 | ~35 | Compact table row |
| **STANDARD** | 50–149 | ~20 | Compact table row |
| **OPTIONAL** | 150+ | 4 | Listed last (external integrations) |

```python
# Filter by intent — reduces 69 tools to ~15 relevant ones (77% fewer tokens)
list_all_tools(intent="rig a character")
list_all_tools(intent="physics simulation")
list_all_tools(intent="export to Unity")
```

Supports English, Turkish, and French intent keywords.

---

## Key Features

<details>
<summary><strong>Assembly Intelligence</strong></summary>

`get_scene_graph ANALYZE_ASSEMBLY` gives a full integrity report:
- **BVH surface-gap measurement** — actual vertex-to-face distance, not just bounding-box
- **INTERPENETRATION detection** — BVH face overlap, not just AABB
- **Hierarchy-aware pairs** — always tests parent↔child; skips distant objects to avoid O(N²)
- **Production readiness score 0–100** — manifold geometry, UV maps, materials, naming, origin alignment

</details>

<details>
<summary><strong>Thread Safety</strong></summary>

All `bpy` calls from the TCP socket thread are automatically marshalled to Blender's main thread via `bpy.app.timers`. This prevents `EXCEPTION_ACCESS_VIOLATION` crashes that occur when background threads touch the Blender API.

</details>

<details>
<summary><strong>Multilingual Intent Routing</strong></summary>

`list_all_tools(intent="...")` accepts keywords in English, Turkish, and French. The intent router maps your description to tool categories and returns only the relevant subset — dramatically reducing context window usage.

</details>

<details>
<summary><strong>Visual Verification</strong></summary>

`get_viewport_screenshot_base64` captures the viewport and returns it as base64 the AI can see:
- Multi-view in one call: `FRONT`, `RIGHT`, `TOP`, `ISOMETRIC`
- `gap_focus_m` — zoom precisely to the scale of an assembly gap
- `target_objects` — frame specific parts, not the whole scene

</details>

---

## Testing

Unit tests run without Blender — `bpy` is mocked with `unittest.mock.MagicMock`. The live
integration suite must use a disposable Blender profile and disposable assets.

```bash
uv run pytest tests/unit -q              # 535 unit tests, ~1.6s
uv run pytest --collect-only -q          # 583 total cases currently collected
uv run pytest tests -v --cov=blender_mcp # With coverage report
uv run python scripts/quality/run_checks.py --fast   # 8 quality checks
uv run python scripts/quality/run_checks.py          # 12 quality checks
```

<details>
<summary>Coverage map — 20 unit test files</summary>

| Module | Test File | Tests |
|--------|-----------|-------|
| Protocol, authentication, transport, policy, and privacy | 5 files | 57 |
| Dispatcher and bridge routing/validation | 3 files | 57 |
| Existing handlers and core behavior | 12 files | 421 |
| **Total** | **20 test files** | **535** |

</details>

See [tests/TESTS.md](tests/TESTS.md) for full documentation.

---

## Developer Guide

<details>
<summary><strong>Environment setup</strong></summary>

```bash
uv sync --all-extras          # Install all dependencies
```

</details>

<details>
<summary><strong>Quality gates</strong></summary>

```bash
make check           # 12 checks: lint + format + type-check + tests
make check-fast      # 8 checks: lint + custom checks
make test-fast       # Unit tests only, stop on first failure
```

</details>

<details>
<summary><strong>Tool inspection</strong></summary>

```bash
make inspect-summary        # Compact table of all 69 tools
make inspect-essential      # ESSENTIAL tier full detail
uv run python scripts/inspect_tools.py --tool get_scene_graph
uv run python scripts/inspect_tools.py --cat animation
```

</details>

<details>
<summary><strong>Adding a new handler</strong></summary>

Create a file in `blender_mcp/handlers/` with `@register_handler`. Auto-discovered — no list to update.

```python
from ..dispatcher import register_handler

@register_handler(
    "manage_my_feature",
    actions=["CREATE", "DELETE", "LIST"],
    schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["CREATE", "DELETE", "LIST"]},
            "name":   {"type": "string"},
        },
        "required": ["action"],
    },
    category="scene",
    priority=60,          # 1-9=ESSENTIAL, 10-49=CORE, 50-149=STANDARD, 150+=OPTIONAL
    description="STANDARD — Create, delete, and list my features.",
)
def manage_my_feature(action, **params):
    if action == "CREATE":
        return {"created": params.get("name")}
    elif action == "LIST":
        return {"items": []}
```

</details>

<details>
<summary><strong>Scripts reference</strong></summary>

See [scripts/SCRIPTS.md](scripts/SCRIPTS.md) for comprehensive documentation of every script.

```bash
python -m blender_mcp --help        # Quick CLI overview
python -m blender_mcp --list-tools  # List all tools
python -m blender_mcp --status      # Project health
```

</details>

---

## Project Structure

<details>
<summary>Click to expand</summary>

```
Blender_mcp/
├── blender_mcp/                     Blender addon (installs via ZIP)
│   ├── __init__.py                  Addon entry + BlenderMCPServer + N-panel UI
│   ├── __main__.py                  python -m blender_mcp CLI
│   ├── dispatcher.py                Handler registry + dispatch_command
│   ├── __version__.py               Single source of version truth
│   ├── handlers/                    52 handler modules
│   │   ├── manage_scene_comprehension.py   get_scene_graph (11 actions)
│   │   ├── manage_rendering.py             RENDER_FRAME, screenshot
│   │   ├── manage_scripting.py             execute_blender_code
│   │   ├── manage_agent_context.py         GET_PRIMER, GET_TACTICS
│   │   ├── manage_history.py               Session checkpoints
│   │   ├── get_local_transforms.py         Parent-relative coordinates
│   │   ├── polyhaven_handler.py            Polyhaven CC0 assets
│   │   ├── sketchfab_handler.py            Sketchfab 3D models
│   │   ├── hunyuan_handler.py              Hunyuan3D AI generation
│   │   ├── hyper3d_handler.py              Hyper3D Rodin AI generation
│   │   └── ...                             40+ more handlers
│   └── core/
│       ├── protocol.py              Wire protocol (4-byte header + JSON)
│       ├── thread_safety.py         Main-thread routing via bpy.app.timers
│       ├── execution_engine.py      Safe bpy.ops + ExecutionPolicy
│       ├── parameter_validator.py   Type coercion + schema validation
│       ├── intent_router.py         Multi-language intent classification
│       ├── semantic_memory.py       Tag-based object resolution
│       ├── security.py              Capability authorization / Safe Mode
│       ├── job_manager.py           Async subprocess queue
│       ├── response_builder.py      Structured response format
│       └── error_protocol.py        280+ ErrorCode enum values
├── stdio_bridge.py                  MCP bridge (runs outside Blender)
├── scripts/
│   ├── SCRIPTS.md                   Full scripts documentation
│   ├── inspect_tools.py             Tool catalog inspector
│   ├── count_tools.py               Handler count audit
│   ├── sync_version.py              Version synchronization
│   ├── test_blender_imports.py      Runtime import validation (needs Blender)
│   ├── remove_unused_ignores.py     mypy cleanup helper
│   └── quality/
│       ├── run_checks.py            Quality gate (8–12 checks)
│       ├── check_version.py         Version consistency
│       ├── check_forbidden.py       Fatal Blender 5.x pattern scanner
│       ├── check_handler_completeness.py  Registry audit
│       ├── check_handler_imports.py Import style validator
│       ├── check_schemas.py         JSON schema validator
│       ├── check_tool_groups.py     Tool group integrity
│       └── lint_imports.py          Import architecture rules
├── tests/
│   ├── unit/                        535 unit tests (20 files, no Blender needed)
│   ├── integration/                 48 collected mock + live integration cases
│   └── TESTS.md                     Test suite documentation
├── docs/
│   └── ARCHITECTURE.md              System design, wire protocol, thread model
├── create_release_zip.py            Builds the addon ZIP for Blender
├── Makefile                         Developer shortcuts
├── pyproject.toml                   Build + lint + type-check config
└── LICENSE                          MIT License
```

</details>

---

## Contribution Guidelines

1. Use `@register_handler` decorator on all handlers — no exceptions
2. `@ensure_main_thread` on any handler that calls `bpy`
3. No `bpy.types.SimpleNamespace` — not available in Blender 5.x
4. Raw Python routes are explicitly classified `EXECUTE_CODE` and separately authorized
5. Use `MCPLogger` / `get_logger()` instead of `print()`
6. Run `make check` before committing — all 12 checks must pass
7. Add unit tests for new logic in `tests/unit/`

---

## Log Files

| Component | Windows | macOS / Linux |
|-----------|---------|---------------|
| Addon (Blender side) | `%TEMP%\blender_server_debug.log` | `/tmp/blender_server_debug.log` |
| Bridge (MCP side) | `%TEMP%\mcp_bridge_debug.log` | `/tmp/mcp_bridge_debug.log` |

---

## License

[MIT License](LICENSE) — © 2026 GÖKSEL ÖZKAN
