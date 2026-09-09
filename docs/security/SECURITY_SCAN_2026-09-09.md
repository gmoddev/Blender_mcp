# Security Review: blender mcp

## Scope

Repository-wide security scan of the current Blender MCP working tree under the approved root `SECURITY.md`.

- Scan mode: repository
- Target kind: git_worktree
- Target ID: target_sha256_2a134773b010adb740fd8707b1e52a5a5c9a92866b30130dc7c53b865cd7ce3c
- Revision: 21e8048ec2e28f974e2d06d937bfd7d18182a52b
- Snapshot digest: codex-security-snapshot/v1:sha256:8669ae163809dd1c0f61150d8530f41ac32feb37f8c683f0aab7b5728c4af0e1
- Inventory strategy: repository
- Included paths: .
- Excluded paths: none
- Runtime or test status: No application code or external services were executed. `uv` and `pytest` were unavailable, so tests were not run.
- Artifacts reviewed: SECURITY.md, AGENTS.md, stdio_bridge.py, blender_mcp/__init__.py, blender_mcp/dispatcher.py, blender_mcp/core/protocol.py, blender_mcp/core/security.py, blender_mcp/core/thread_safety.py, blender_mcp/core/parameter_validator.py, blender_mcp/core/logging_config.py, blender_mcp/core/job_manager.py, blender_mcp/core/export_pipeline.py, blender_mcp/handlers/manage_scripting.py, blender_mcp/handlers/manage_scene.py, blender_mcp/handlers/manage_export.py, blender_mcp/handlers/manage_export_pipeline.py, blender_mcp/handlers/manage_rendering.py, blender_mcp/handlers/manage_batch.py, blender_mcp/handlers/hunyuan_handler.py, blender_mcp/handlers/polyhaven_handler.py, blender_mcp/handlers/sketchfab_handler.py, blender_mcp/handlers/hyper3d_handler.py, blender_mcp/utils/path.py, blender_mcp/utils/path_validator.py, create_release_zip.py, tests/unit/test_security.py, tests/unit/test_protocol.py, docs/ARCHITECTURE.md, docs/SECURITY_INVARIANTS.md, docs/adr/0002-command-lifecycle-and-timeouts.md
- Scan context: The threat model was generated during this scan from effective source behavior. The review prioritized command lifecycle, correlation/framing, local peer authentication, Safe Mode, arbitrary Python, logging/telemetry, integrations, paths/URLs, release integrity, and protected `.blend` assets.

Limitations and exclusions:
- Coverage is partial because live Blender semantics, OS ACLs, external endpoints, and native importer behavior were not dynamically verified.
- The scan used three usable worker slots rather than the six suggested by preflight and compensated with sequential primary validation.
- Findings describe the current mutable working tree, including uncommitted policy and architecture documents.

### Scan Summary

| Field | Value |
| --- | --- |
| Scan outcome | completed |
| Reportable findings | 11 |
| Severity mix | high: 3, medium: 8 |
| Confidence mix | high: 11 |
| Coverage | partial |
| Validation mode | Offline static source trace with independent baseline, architecture, external-boundary, and transport/lifecycle reviews. |

Canonical artifacts: `scan-manifest.json`, `findings.json`, and `coverage.json`. This report is a deterministic projection of those files.

## Threat Model

Blender MCP is a local-first two-process control plane. An MCP client launches `stdio_bridge.py`, which parses newline-delimited JSON-RPC and forwards tool names and arguments over a length-prefixed IPv4 TCP connection. Inside Blender, an operator-started loopback listener creates a thread per connection, dispatches registered handlers through a main-thread queue, and exposes scene reads, mutations, file I/O, subprocess rendering, external integrations, and multiple arbitrary-Python paths (`stdio_bridge.py:42-58`, `blender_mcp/__init__.py:162-188`, `blender_mcp/handlers/manage_scripting.py:67-314`).

### Assets

- Integrity and availability of the live Blender scene, undo state, current `.blend` file, linked assets, render settings, and generated outputs.
- Blender-process and OS-user authority exposed through raw Python with normal builtins and `bpy`.
- Local filesystem read/write authority used by scene I/O, exporters, captures, downloaded assets, and render snapshots.
- Request identity, ordering, execution state, retry safety, and truthful timeout outcomes.
- Sketchfab, Hunyuan, and conditional Hyper3D credentials plus confidential prompts and source images.
- Privacy and audit data stored in bridge, server, and structured Blender logs.
- Integrity and provenance of the installable add-on ZIP and dependency resolution.

### Trust Boundaries

- MCP client to stdio bridge: the client controls JSON-RPC method, tool name, and arguments (`stdio_bridge.py:196-352`).
- Stdio bridge to Blender TCP server: normal traffic targets `localhost:9879` and contains only `tool` and `params`; no peer token, protocol version, or stable inner request ID is added (`stdio_bridge.py:42-58`, `stdio_bridge.py:351-352`).
- TCP bytes to parsed commands: a four-byte unsigned length selects the amount of UTF-8 JSON read, with no application maximum (`blender_mcp/core/protocol.py:35-78`).
- Network threads to Blender main thread: the dispatcher places `MCPCommand` objects into a shared timer-driven queue whose timeout transitions are not synchronized (`blender_mcp/core/thread_safety.py:253-363`).
- Dispatcher to handlers: registry/schema checks exist, but authorization delegates to `SecurityManager.validate_action()`, which always allows (`blender_mcp/dispatcher.py:547-628`, `blender_mcp/core/security.py:27-31`).
- Blender to local files: path helpers normalize and create paths but do not define an authorized root (`blender_mcp/utils/path.py:7-85`, `blender_mcp/utils/path_validator.py:13-49`).
- Blender to external services and imported content: fixed API origins coexist with caller/service-provided secondary URLs, unbounded response bodies, and archive/import boundaries.
- Repository source to release ZIP: the local builder emits an unsigned, unchecked archive, but no runtime updater or download consumer exists in scope.

### Attacker Capabilities

- The policy treats the MCP client, request fields, raw-code bodies, paths, URLs, frame lengths, downloaded assets, archive contents, scene metadata, and external responses as untrusted.
- A local process can speak the length-prefixed protocol while the operator-started listener is running; no Blender UI or repository control is assumed.
- An untrusted MCP client can invoke advertised structured tools and, if authorization is ineffective, raw-code handlers with Blender-process authority.
- A caller can choose local paths for scene I/O, exports, Hunyuan images, and archive URLs, subject only to the Blender identity's OS access.
- An external service adversary can influence secondary download URLs and model/archive bytes; compromise of TLS or Blender's native importers is not assumed.

### Security Objectives

- Authenticate and correlate every bounded protocol frame before dispatch; reject missing, invalid, oversized, duplicate, or mismatched envelopes.
- Keep command states atomic, monotonic, observable, and bound to stable request IDs; reconcile indeterminate mutations before retry.
- Keep Blender API access on the main thread while ensuring untrusted preprocessing cannot block that thread.
- Make Safe Mode deny arbitrary Python and destructive mutations by default, with unknown states failing closed.
- Constrain filesystem paths, URLs, frame lengths, archive contents, downloaded bytes, and text-processing complexity at their consumers.
- Protect `.blend` assets through truthful outcomes, durable reconciliation, explicit overwrite approval, and recoverable checkpoints.
- Log request identity, action, lifecycle state, and timing without code, prompts, credentials, arbitrary parameters, or scene-sensitive payloads.
- Keep credentials outside saved scene data and prevent silent runtime source replacement.

### Assumptions

- Scope is the entire 173-file current working tree at HEAD `21e8048ec2e28f974e2d06d937bfd7d18182a52b`, including the user's uncommitted policy and architecture documents.
- The normal add-on listener is started by a UI operator; README claims of automatic startup are not effective source behavior.
- The normal bridge entry point uses literal `localhost:9879`; documented `BLENDER_HOST`, `BLENDER_PORT`, and `MCP_TRANSPORT` environment settings are not consumed.
- Safe Mode defaults true in UI but is not wired to effective authorization.
- No outbound telemetry sender or runtime self-update path was found.
- Hyper3D's handler consumes configuration property names that are not registered by the UI, limiting current reachability.
- External HTTP behavior, native Blender importer safety, OS ACLs, loopback peer isolation, and live main-thread timing were not executed during this offline review.

## Findings

| Finding | Severity | Confidence | Detailed write-up |
| --- | --- | --- | --- |
| [Hunyuan generation can upload any Blender-readable local file to Tencent](#finding-1) | high | high | inline below |
| [Enabling Safe Mode still allows arbitrary Python and destructive scene mutations](#finding-2) | high | high | inline below |
| [Starting the Blender server lets any local process execute code as the Blender user](#finding-3) | high | high | inline below |
| [Importing a Hunyuan ZIP can send blind requests to private and loopback services](#finding-4) | medium | high | inline below |
| [A timed-out mutation can run later or be replayed after a lost response](#finding-5) | medium | high | inline below |
| [Running tools writes code, prompts, paths, and response data to persistent debug logs](#finding-6) | medium | high | inline below |
| [A local client can exhaust Blender with oversized frames and blocking connections](#finding-7) | medium | high | inline below |
| [External downloads can freeze Blender and exhaust memory or disk](#finding-8) | medium | high | inline below |
| [Saving or rendering a scene can copy API credentials into distributable `.blend` files](#finding-9) | medium | high | inline below |
| [Agent-supplied paths can escape the workspace and overwrite protected files](#finding-10) | medium | high | inline below |
| [A crafted batch-selection pattern can freeze Blender's main thread](#finding-11) | medium | high | inline below |

### Confidence Scale

| Label | Meaning |
| --- | --- |
| high | Direct evidence supports the finding with no material unresolved blocker. |
| medium | Evidence supports a plausible issue, but material runtime or reachability proof remains. |
| low | Evidence is incomplete and the item is retained only for explicit follow-up. |

<a id="finding-1"></a>

### [1] Hunyuan generation can upload any Blender-readable local file to Tencent

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | The caller-controlled path reaches `open(..., rb)` and a third-party POST body directly, with no intervening containment or file-type check. |
| Category | Arbitrary local file disclosure |
| CWE | CWE-200, CWE-73 |
| Affected lines | blender_mcp/handlers/hunyuan_handler.py:43-46, blender_mcp/handlers/hunyuan_handler.py:199-210 |

#### Summary

The Hunyuan schema accepts an unrestricted `image_path`. In official mode, every non-HTTP value is opened, read in full, base64-encoded, and included in the outbound Tencent request without root, type, size, or consent checks.

#### Root Cause

The integration uses a string-prefix test as its only distinction between URL and file inputs. It delegates local path selection entirely to the MCP caller, then reads the file without containment, type/size validation, user confirmation, or an enforced integration-enable guard.

**Schema accepts a local path or URL** — `blender_mcp/handlers/hunyuan_handler.py:41-50`

`image_path` is an unrestricted caller string and is explicitly allowed to name a local file.

```python
"prompt": {"type": "string", "description": "Text prompt for generation."},
"image_path": {
    "type": "string",
    "description": "Local path or URL to image.",
},
...
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**GENERATE forwards the path without enforcing the enable flag** — `blender_mcp/handlers/hunyuan_handler.py:73-83`

Only STATUS reads presentation state; GENERATE passes the attacker path directly to the job creator.

```python
if action == HunyuanAction.STATUS.value:
    return _get_status()
if action == HunyuanAction.GENERATE.value:
    return _create_job(params.get("prompt"), params.get("image_path"))
...
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**Non-HTTP input is read and sent in the request body** — `blender_mcp/handlers/hunyuan_handler.py:196-210`

Every non-HTTP string reaches a local file read, and its complete contents are serialized to the fixed Tencent endpoint.

```python
data: Dict[str, Any] = {"Num": 1}
if text_prompt:
    data["Prompt"] = text_prompt
if image:
    if image.startswith("http"):
        data["ImageUrl"] = image
    else:
        with open(image, "rb") as f:
            data["ImageBase64"] = base64.b64encode(f.read()).decode("ascii")
...
resp = requests.post(endpoint, headers=headers, data=json.dumps(data))
```

#### Validation

The source trace establishes input, local file read, base64 transformation, and outbound POST. The destination is fixed HTTPS, which limits recipient selection but not the disclosure.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Schema accepts a local path or URL** — `blender_mcp/handlers/hunyuan_handler.py:41-50`

`image_path` is an unrestricted caller string and is explicitly allowed to name a local file.

```python
"prompt": {"type": "string", "description": "Text prompt for generation."},
"image_path": {
    "type": "string",
    "description": "Local path or URL to image.",
},
...
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**GENERATE forwards the path without enforcing the enable flag** — `blender_mcp/handlers/hunyuan_handler.py:73-83`

Only STATUS reads presentation state; GENERATE passes the attacker path directly to the job creator.

```python
if action == HunyuanAction.STATUS.value:
    return _get_status()
if action == HunyuanAction.GENERATE.value:
    return _create_job(params.get("prompt"), params.get("image_path"))
...
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**Non-HTTP input is read and sent in the request body** — `blender_mcp/handlers/hunyuan_handler.py:196-210`

Every non-HTTP string reaches a local file read, and its complete contents are serialized to the fixed Tencent endpoint.

```python
data: Dict[str, Any] = {"Num": 1}
if text_prompt:
    data["Prompt"] = text_prompt
if image:
    if image.startswith("http"):
        data["ImageUrl"] = image
    else:
        with open(image, "rb") as f:
            data["ImageBase64"] = base64.b64encode(f.read()).decode("ascii")
...
resp = requests.post(endpoint, headers=headers, data=json.dumps(data))
```

Assertions:
- The MCP caller controls `image_path`.
- Non-HTTP values are opened directly.
- The complete bytes are sent in an external request body.

Counterevidence and remaining uncertainty:
- The destination is a fixed HTTPS Tencent endpoint.
- The optional `requests` dependency and official mode must be available.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

MCP `image_path` -\> `_create_job()` -\> `_create_job_official()` -\> `open()`/base64 -\> `requests.post()`

- **Source:** caller-controlled local `image_path`

- **Sink:** Tencent Hunyuan POST body

- **Outcome:** disclosure of any Blender-readable local file to a third party

**Schema accepts a local path or URL** — `blender_mcp/handlers/hunyuan_handler.py:41-50`

`image_path` is an unrestricted caller string and is explicitly allowed to name a local file.

```python
"prompt": {"type": "string", "description": "Text prompt for generation."},
"image_path": {
    "type": "string",
    "description": "Local path or URL to image.",
},
...
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**GENERATE forwards the path without enforcing the enable flag** — `blender_mcp/handlers/hunyuan_handler.py:73-83`

Only STATUS reads presentation state; GENERATE passes the attacker path directly to the job creator.

```python
if action == HunyuanAction.STATUS.value:
    return _get_status()
if action == HunyuanAction.GENERATE.value:
    return _create_job(params.get("prompt"), params.get("image_path"))
...
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**Non-HTTP input is read and sent in the request body** — `blender_mcp/handlers/hunyuan_handler.py:196-210`

Every non-HTTP string reaches a local file read, and its complete contents are serialized to the fixed Tencent endpoint.

```python
data: Dict[str, Any] = {"Num": 1}
if text_prompt:
    data["Prompt"] = text_prompt
if image:
    if image.startswith("http"):
        data["ImageUrl"] = image
    else:
        with open(image, "rb") as f:
            data["ImageBase64"] = base64.b64encode(f.read()).decode("ascii")
...
resp = requests.post(endpoint, headers=headers, data=json.dumps(data))
```

#### Reachability

Any MCP client can invoke the advertised integration when `requests` is installed; the default-false UI toggle is not enforced on this path.

- **Attacker:** untrusted or compromised MCP client

- **Entry point:** `integration_hunyuan` GENERATE action

- **Outcome:** disclosure of any Blender-readable local file to a third party

Preconditions:
- `requests` is installed.
- Hunyuan mode is `OFFICIAL_API`.
- The Blender process can read the selected file.

**Schema accepts a local path or URL** — `blender_mcp/handlers/hunyuan_handler.py:41-50`

`image_path` is an unrestricted caller string and is explicitly allowed to name a local file.

```python
"prompt": {"type": "string", "description": "Text prompt for generation."},
"image_path": {
    "type": "string",
    "description": "Local path or URL to image.",
},
...
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**GENERATE forwards the path without enforcing the enable flag** — `blender_mcp/handlers/hunyuan_handler.py:73-83`

Only STATUS reads presentation state; GENERATE passes the attacker path directly to the job creator.

```python
if action == HunyuanAction.STATUS.value:
    return _get_status()
if action == HunyuanAction.GENERATE.value:
    return _create_job(params.get("prompt"), params.get("image_path"))
...
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**Non-HTTP input is read and sent in the request body** — `blender_mcp/handlers/hunyuan_handler.py:196-210`

Every non-HTTP string reaches a local file read, and its complete contents are serialized to the fixed Tencent endpoint.

```python
data: Dict[str, Any] = {"Num": 1}
if text_prompt:
    data["Prompt"] = text_prompt
if image:
    if image.startswith("http"):
        data["ImageUrl"] = image
    else:
        with open(image, "rb") as f:
            data["ImageBase64"] = base64.b64encode(f.read()).decode("ascii")
...
resp = requests.post(endpoint, headers=headers, data=json.dumps(data))
```

#### Severity

**High** — A normal MCP request can disclose any file readable by Blender to a third party. The feature requires the optional HTTP dependency and official mode, but no additional exploit primitive or trusted-service compromise.

Lower the rating after the input is restricted to explicitly user-approved, verified image files with a transmission confirmation; raise urgency where Blender can read developer or cloud credentials.

Impact assessment:
- **Level:** high
- **Why:** SSH keys, source, credentials, private images, and project files may be transmitted externally.

Likelihood assessment:
- **Level:** high
- **Why:** The path is directly controlled and no image parsing is required before the outbound send.

#### Remediation

Enforce the integration enable flag, restrict inputs to explicitly approved image roots, verify image type and a small size limit, and require clear user consent before transmitting local content.

Tests:
- Reject paths outside the approved image root.
- Reject a text/credential file renamed with an image extension.
- Assert disabled Hunyuan mode performs no file or network I/O.

Preventive controls:
- Use a local file picker or opaque approved-file handle.
- Bound reads before encoding.
- Display the destination and selected file before transmission.

<a id="finding-2"></a>

### [2] Enabling Safe Mode still allows arbitrary Python and destructive scene mutations

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | The control is an unconditional return, the dispatcher relies on it, and existing tests explicitly expect dangerous and unknown actions to be allowed. |
| Category | Missing authorization / fail-open security mode |
| CWE | CWE-862 |
| Affected lines | blender_mcp/core/security.py:27-31, blender_mcp/dispatcher.py:547-555, blender_mcp/handlers/manage_scripting.py:67-101 |

#### Summary

Safe Mode defaults on and the UI states that arbitrary code is blocked, but the dispatch authorization function ignores the preference and always returns true. Raw Python and destructive handlers therefore remain callable in every mode.

#### Root Cause

The UI and preference define Safe Mode, but `SecurityManager.validate_action()` does not consult it or classify capabilities. Because the dispatcher uses this unconditional return as its authorization boundary, every registered action—including multiple raw-code paths—remains allowed.

**Safe Mode promises code execution is blocked** — `blender_mcp/__init__.py:386-403`

The user-visible preference establishes a default-deny expectation for arbitrary Python.

```python
safe_mode: bool = cast(
    bool,
    BoolProperty(
        name="Safe Mode",
        description="Prevent execution of arbitrary Python code. Recommended for shared environments.",
        default=True,
    ),
)
...
if self.safe_mode:
    box.label(text="Arbitrary code execution is BLOCKED.", icon="CHECKMARK")
```

**Dispatcher trusts the no-op control** — `blender_mcp/dispatcher.py:547-555`

The only Safe Mode decision is the unconditional result, so dispatch continues to registered handlers.

```python
if not SecurityManager.validate_action(tool_name, action):
    error_result = {
        "error": f"Security Violation: '{tool_name}' is blocked in Safe Mode.",
        "code": "SECURITY_VIOLATION",
        "is_security_violation": True,
    }
    return error_result
```

**Authorization ignores Safe Mode and action identity** — `blender_mcp/core/security.py:27-31`

`tool_name`, `action_name`, and the readable Safe Mode preference are discarded, so every capability is authorized.

```python
def validate_action(tool_name: str, action_name: str) -> bool:
    """
    HIGH MODE ENABLED: All actions are permitted.
    """
    return True
```

**Structured scripting action reaches `exec`** — `blender_mcp/handlers/manage_scripting.py:67-101`

Caller Python reaches `exec` after the ineffective authorization decision.

```python
if action == ScriptingAction.EXECUTE_CODE.value:
    code = params.get("code")
    ...
    exec_globals = {"bpy": bpy, "__name__": "__main__"}
    ...
    exec(code, exec_globals)
```

#### Validation

The source shows a complete preference-to-dispatch disconnect, and `tests/unit/test_security.py` codifies unconditional authorization instead of fail-closed behavior.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Safe Mode promises code execution is blocked** — `blender_mcp/__init__.py:386-403`

The user-visible preference establishes a default-deny expectation for arbitrary Python.

```python
safe_mode: bool = cast(
    bool,
    BoolProperty(
        name="Safe Mode",
        description="Prevent execution of arbitrary Python code. Recommended for shared environments.",
        default=True,
    ),
)
...
if self.safe_mode:
    box.label(text="Arbitrary code execution is BLOCKED.", icon="CHECKMARK")
```

**Authorization ignores Safe Mode and action identity** — `blender_mcp/core/security.py:27-31`

`tool_name`, `action_name`, and the readable Safe Mode preference are discarded, so every capability is authorized.

```python
def validate_action(tool_name: str, action_name: str) -> bool:
    """
    HIGH MODE ENABLED: All actions are permitted.
    """
    return True
```

**Dispatcher trusts the no-op control** — `blender_mcp/dispatcher.py:547-555`

The only Safe Mode decision is the unconditional result, so dispatch continues to registered handlers.

```python
if not SecurityManager.validate_action(tool_name, action):
    error_result = {
        "error": f"Security Violation: '{tool_name}' is blocked in Safe Mode.",
        "code": "SECURITY_VIOLATION",
        "is_security_violation": True,
    }
    return error_result
```

**Structured scripting action reaches `exec`** — `blender_mcp/handlers/manage_scripting.py:67-101`

Caller Python reaches `exec` after the ineffective authorization decision.

```python
if action == ScriptingAction.EXECUTE_CODE.value:
    code = params.get("code")
    ...
    exec_globals = {"bpy": bpy, "__name__": "__main__"}
    ...
    exec(code, exec_globals)
```

Assertions:
- Safe Mode defaults true and promises Python denial.
- `validate_action()` always returns true.
- Caller code reaches an `exec` sink after that check.

Counterevidence and remaining uncertainty:
- Action/schema validation checks request shape, not authorization.
- A regex blocks one synchronous-render expression but is not a Python sandbox.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

MCP tool/action -\> `validate_action()` -\> unconditional allow -\> registered handler -\> raw code or destructive Blender sink

- **Source:** an MCP request made while Safe Mode is enabled

- **Sink:** raw Python `exec` or a destructive structured Blender operation

- **Outcome:** arbitrary Python execution or scene/file mutation despite the user's explicit security setting

**Safe Mode promises code execution is blocked** — `blender_mcp/__init__.py:386-403`

The user-visible preference establishes a default-deny expectation for arbitrary Python.

```python
safe_mode: bool = cast(
    bool,
    BoolProperty(
        name="Safe Mode",
        description="Prevent execution of arbitrary Python code. Recommended for shared environments.",
        default=True,
    ),
)
...
if self.safe_mode:
    box.label(text="Arbitrary code execution is BLOCKED.", icon="CHECKMARK")
```

**Authorization ignores Safe Mode and action identity** — `blender_mcp/core/security.py:27-31`

`tool_name`, `action_name`, and the readable Safe Mode preference are discarded, so every capability is authorized.

```python
def validate_action(tool_name: str, action_name: str) -> bool:
    """
    HIGH MODE ENABLED: All actions are permitted.
    """
    return True
```

**Dispatcher trusts the no-op control** — `blender_mcp/dispatcher.py:547-555`

The only Safe Mode decision is the unconditional result, so dispatch continues to registered handlers.

```python
if not SecurityManager.validate_action(tool_name, action):
    error_result = {
        "error": f"Security Violation: '{tool_name}' is blocked in Safe Mode.",
        "code": "SECURITY_VIOLATION",
        "is_security_violation": True,
    }
    return error_result
```

**Structured scripting action reaches `exec`** — `blender_mcp/handlers/manage_scripting.py:67-101`

Caller Python reaches `exec` after the ineffective authorization decision.

```python
if action == ScriptingAction.EXECUTE_CODE.value:
    code = params.get("code")
    ...
    exec_globals = {"bpy": bpy, "__name__": "__main__"}
    ...
    exec(code, exec_globals)
```

#### Reachability

Any MCP caller with transport access can exercise the bypass; authentication would not fix it because authorization remains unconditional.

- **Attacker:** untrusted or compromised MCP client

- **Entry point:** any registered raw-code or destructive tool

- **Outcome:** arbitrary Python execution or scene/file mutation despite the user's explicit security setting

Preconditions:
- The MCP caller can submit a registered action.
- Safe Mode may be on or off; the result is identical.

**Safe Mode promises code execution is blocked** — `blender_mcp/__init__.py:386-403`

The user-visible preference establishes a default-deny expectation for arbitrary Python.

```python
safe_mode: bool = cast(
    bool,
    BoolProperty(
        name="Safe Mode",
        description="Prevent execution of arbitrary Python code. Recommended for shared environments.",
        default=True,
    ),
)
...
if self.safe_mode:
    box.label(text="Arbitrary code execution is BLOCKED.", icon="CHECKMARK")
```

**Authorization ignores Safe Mode and action identity** — `blender_mcp/core/security.py:27-31`

`tool_name`, `action_name`, and the readable Safe Mode preference are discarded, so every capability is authorized.

```python
def validate_action(tool_name: str, action_name: str) -> bool:
    """
    HIGH MODE ENABLED: All actions are permitted.
    """
    return True
```

**Dispatcher trusts the no-op control** — `blender_mcp/dispatcher.py:547-555`

The only Safe Mode decision is the unconditional result, so dispatch continues to registered handlers.

```python
if not SecurityManager.validate_action(tool_name, action):
    error_result = {
        "error": f"Security Violation: '{tool_name}' is blocked in Safe Mode.",
        "code": "SECURITY_VIOLATION",
        "is_security_violation": True,
    }
    return error_result
```

**Structured scripting action reaches `exec`** — `blender_mcp/handlers/manage_scripting.py:67-101`

Caller Python reaches `exec` after the ineffective authorization decision.

```python
if action == ScriptingAction.EXECUTE_CODE.value:
    code = params.get("code")
    ...
    exec_globals = {"bpy": bpy, "__name__": "__main__"}
    ...
    exec(code, exec_globals)
```

#### Severity

**High** — This bypasses an explicit default-on security boundary and reaches unrestricted Python plus destructive Blender operations. Any connected MCP caller can trigger it without an additional race or configuration change.

Lower the rating only after tests prove Safe Mode denies all `EXECUTE_CODE` and destructive action classes before handler invocation.

Impact assessment:
- **Level:** high
- **Why:** The bypass reaches unrestricted Python and destructive asset operations.

Likelihood assessment:
- **Level:** high
- **Why:** The bypass is deterministic and requires only a normal registered request.

#### Remediation

Classify every handler action as `READ`, `MUTATE`, or `EXECUTE_CODE`. Make `validate_action()` consult Safe Mode and deny raw code plus destructive mutations before handler invocation, with unknown or missing classifications denied.

Tests:
- Assert Safe Mode rejects `execute_blender_code`, `manage_scripting.EXECUTE_CODE`, and text-block execution.
- Assert unknown, empty, and unclassified actions fail closed.
- Assert disabling Safe Mode is an explicit user action and accurately changes the UI and effective policy.

Preventive controls:
- Centralize capability metadata in the handler registry.
- Separate raw-code enablement from structured mutation authority.
- Keep denial decisions before parameter-dependent side effects.

<a id="finding-3"></a>

### [3] Starting the Blender server lets any local process execute code as the Blender user

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Two independent source traces confirm an unauthenticated accept-to-dispatch path and a directly reachable unrestricted `exec` sink. |
| Category | Missing authentication for a critical function |
| CWE | CWE-306 |
| Affected lines | blender_mcp/__init__.py:227-275, blender_mcp/handlers/manage_scripting.py:224-271 |

#### Summary

The loopback TCP server accepts and dispatches commands without authenticating the peer. A local process that reaches the predictable port can invoke `execute_blender_code`, which runs caller Python with `bpy` and normal builtins in Blender's OS-user context.

#### Root Cause

The server treats loopback reachability as caller identity. It creates a per-client handler and dispatches the first frame before any handshake, token comparison, OS peer check, or authenticated session exists; the registered raw-code handler then exposes full process authority.

**Accepted sockets receive no authentication handshake** — `blender_mcp/__init__.py:227-233`

Every loopback peer receives a handler thread; no credential or authenticated connection state is created before `_handle_client`.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
```

**The first decoded command is dispatched directly** — `blender_mcp/__init__.py:256-275`

Attacker-controlled frame data is decoded and forwarded to command dispatch without an identity check.

```python
client.settimeout(None)
...
command = protocol.recv_message(client)
if not command:
    break
...
response = self.execute_command(command)
```

**Registered tool executes caller code with normal builtins** — `blender_mcp/handlers/manage_scripting.py:224-271`

`code` from the request reaches Python `exec` with Blender and ordinary builtins, granting the Blender process's OS authority.

```python
code = params.get("code")
...
exec_globals = {
    "bpy": bpy,
    "__builtins__": __builtins__,
}
...
exec(code, exec_globals)
```

#### Validation

Repository-wide authentication searches found no transport token, connection handshake, peer-credential verification, or authenticated session state. Third-party API credentials are unrelated to MCP peer authentication.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Accepted sockets receive no authentication handshake** — `blender_mcp/__init__.py:227-233`

Every loopback peer receives a handler thread; no credential or authenticated connection state is created before `_handle_client`.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
```

**The first decoded command is dispatched directly** — `blender_mcp/__init__.py:256-275`

Attacker-controlled frame data is decoded and forwarded to command dispatch without an identity check.

```python
client.settimeout(None)
...
command = protocol.recv_message(client)
if not command:
    break
...
response = self.execute_command(command)
```

**Registered tool executes caller code with normal builtins** — `blender_mcp/handlers/manage_scripting.py:224-271`

`code` from the request reaches Python `exec` with Blender and ordinary builtins, granting the Blender process's OS authority.

```python
code = params.get("code")
...
exec_globals = {
    "bpy": bpy,
    "__builtins__": __builtins__,
}
...
exec(code, exec_globals)
```

Assertions:
- A newly accepted socket can reach dispatch without authentication.
- `execute_blender_code` is registered and uses unrestricted builtins.

Counterevidence and remaining uncertainty:
- The listener defaults to loopback and must be started from Blender's UI.
- Tool and schema checks reject unknown or malformed calls, but they do not identify the caller.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

local TCP bytes -\> `recv_message()` -\> `execute_command()` -\> dispatcher -\> `execute_blender_code()` -\> Python `exec`

- **Source:** an unauthenticated local TCP peer's framed JSON command

- **Sink:** `exec(code, exec_globals)` in the Blender process

- **Outcome:** arbitrary code execution, file access, credential access, and scene or `.blend` destruction

**Accepted sockets receive no authentication handshake** — `blender_mcp/__init__.py:227-233`

Every loopback peer receives a handler thread; no credential or authenticated connection state is created before `_handle_client`.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
```

**The first decoded command is dispatched directly** — `blender_mcp/__init__.py:256-275`

Attacker-controlled frame data is decoded and forwarded to command dispatch without an identity check.

```python
client.settimeout(None)
...
command = protocol.recv_message(client)
if not command:
    break
...
response = self.execute_command(command)
```

**Registered tool executes caller code with normal builtins** — `blender_mcp/handlers/manage_scripting.py:224-271`

`code` from the request reaches Python `exec` with Blender and ordinary builtins, granting the Blender process's OS authority.

```python
code = params.get("code")
...
exec_globals = {
    "bpy": bpy,
    "__builtins__": __builtins__,
}
...
exec(code, exec_globals)
```

#### Reachability

Any local process or local user able to reach the loopback port can trigger the path after the operator starts the server.

- **Attacker:** unauthenticated local process or local user

- **Entry point:** Blender MCP loopback TCP listener

- **Outcome:** arbitrary code execution, file access, credential access, and scene or `.blend` destruction

Preconditions:
- The add-on server is running.
- The attacker can connect to the configured loopback port.

**Accepted sockets receive no authentication handshake** — `blender_mcp/__init__.py:227-233`

Every loopback peer receives a handler thread; no credential or authenticated connection state is created before `_handle_client`.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
```

**The first decoded command is dispatched directly** — `blender_mcp/__init__.py:256-275`

Attacker-controlled frame data is decoded and forwarded to command dispatch without an identity check.

```python
client.settimeout(None)
...
command = protocol.recv_message(client)
if not command:
    break
...
response = self.execute_command(command)
```

**Registered tool executes caller code with normal builtins** — `blender_mcp/handlers/manage_scripting.py:224-271`

`code` from the request reaches Python `exec` with Blender and ordinary builtins, granting the Blender process's OS authority.

```python
code = params.get("code")
...
exec_globals = {
    "bpy": bpy,
    "__builtins__": __builtins__,
}
...
exec(code, exec_globals)
```

#### Severity

**High** — The impact is arbitrary code execution and complete scene/file compromise. The server must be manually running and is loopback-only, which narrows reachability but does not authenticate other local processes or users.

Lower the rating if the deployed listener is protected by an independently verified per-user IPC ACL; raise urgency if the host is shared or processes from lower-trust sandboxes can access loopback.

Impact assessment:
- **Level:** high
- **Why:** The sink has full Blender Python and normal OS-user authority.

Likelihood assessment:
- **Level:** medium
- **Why:** The port is predictable and unauthenticated, but exploitation is local and requires the server to be running.

#### Remediation

Generate a high-entropy per-session credential and require an authenticated, versioned handshake before tool discovery or dispatch. Bind authenticated state to the connection, prefer OS-ACL-protected local IPC where practical, and require a separate capability for raw Python.

Tests:
- Reject a valid tool request sent before authentication.
- Reject an invalid or expired session credential without parsing action parameters.
- Prove an authenticated session cannot use a credential from another Blender process.

Preventive controls:
- Keep a literal loopback bind in addition to authentication.
- Rotate and revoke per-session credentials.
- Audit every raw-code grant as a distinct capability.

<a id="finding-4"></a>

### [4] Importing a Hunyuan ZIP can send blind requests to private and loopback services

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | The MCP URL reaches the HTTP client directly; no central URL allowlist or private-address check exists. |
| Category | Server-side request forgery |
| CWE | CWE-918 |
| Affected lines | blender_mcp/handlers/hunyuan_handler.py:48-50, blender_mcp/handlers/hunyuan_handler.py:154-170 |

#### Summary

The IMPORT action passes caller-supplied `zip_url` directly to `requests.get` with redirects enabled and no scheme, host, resolved-IP, port, or redirect validation.

#### Root Cause

The import path treats a network locator as trusted content metadata. It performs no URL parsing, host/port allowlist, DNS/IP classification, redirect revalidation, or destination binding before issuing the GET.

**IMPORT accepts an unrestricted URL** — `blender_mcp/handlers/hunyuan_handler.py:48-50`

The caller supplies the complete URL; the schema adds no scheme or host constraint.

```python
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**Action forwards the same value** — `blender_mcp/handlers/hunyuan_handler.py:82-83`

No transformation or validation occurs between the MCP parameter and `_import_asset`.

```python
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**HTTP client receives the caller URL directly** — `blender_mcp/handlers/hunyuan_handler.py:154-170`

`requests.get` resolves and follows the attacker destination from the Blender host before any ZIP check.

```python
def _import_asset(zip_url):
    if not zip_url:
        return {"error": "Zip URL required", "code": "MISSING_ZIP_URL"}
    ...
    r = requests.get(zip_url, stream=True)
    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    ...
    z.extractall(temp_dir)
```

#### Validation

No URL validator or central network policy was found. Fixed hosts in other integration metadata calls do not constrain this caller-controlled Hunyuan URL.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**IMPORT accepts an unrestricted URL** — `blender_mcp/handlers/hunyuan_handler.py:48-50`

The caller supplies the complete URL; the schema adds no scheme or host constraint.

```python
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**Action forwards the same value** — `blender_mcp/handlers/hunyuan_handler.py:82-83`

No transformation or validation occurs between the MCP parameter and `_import_asset`.

```python
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**HTTP client receives the caller URL directly** — `blender_mcp/handlers/hunyuan_handler.py:154-170`

`requests.get` resolves and follows the attacker destination from the Blender host before any ZIP check.

```python
def _import_asset(zip_url):
    if not zip_url:
        return {"error": "Zip URL required", "code": "MISSING_ZIP_URL"}
    ...
    r = requests.get(zip_url, stream=True)
    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    ...
    z.extractall(temp_dir)
```

Assertions:
- The caller controls the full request URL.
- The URL reaches `requests.get` unchanged.
- Private, loopback, link-local, and redirect targets are not rejected.

Counterevidence and remaining uncertainty:
- The response is consumed as a ZIP rather than returned as arbitrary text.
- `requests` limits supported schemes, but HTTP(S) private destinations remain reachable.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

MCP `zip_url` -\> `_import_asset()` -\> `requests.get()` -\> internal or loopback service

- **Source:** caller-controlled URL

- **Sink:** HTTP GET from the Blender workstation

- **Outcome:** blind access to otherwise unreachable local/private services

**IMPORT accepts an unrestricted URL** — `blender_mcp/handlers/hunyuan_handler.py:48-50`

The caller supplies the complete URL; the schema adds no scheme or host constraint.

```python
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**Action forwards the same value** — `blender_mcp/handlers/hunyuan_handler.py:82-83`

No transformation or validation occurs between the MCP parameter and `_import_asset`.

```python
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**HTTP client receives the caller URL directly** — `blender_mcp/handlers/hunyuan_handler.py:154-170`

`requests.get` resolves and follows the attacker destination from the Blender host before any ZIP check.

```python
def _import_asset(zip_url):
    if not zip_url:
        return {"error": "Zip URL required", "code": "MISSING_ZIP_URL"}
    ...
    r = requests.get(zip_url, stream=True)
    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    ...
    z.extractall(temp_dir)
```

#### Reachability

Any MCP client can invoke IMPORT when `requests` is installed; no provider credentials are required for this action.

- **Attacker:** untrusted or compromised MCP client

- **Entry point:** `integration_hunyuan` IMPORT action

- **Outcome:** blind access to otherwise unreachable local/private services

Preconditions:
- `requests` is installed.
- The target service is reachable from the workstation.

**IMPORT accepts an unrestricted URL** — `blender_mcp/handlers/hunyuan_handler.py:48-50`

The caller supplies the complete URL; the schema adds no scheme or host constraint.

```python
"zip_url": {
    "type": "string",
    "description": "URL of ZIP file to import (for IMPORT action).",
},
```

**Action forwards the same value** — `blender_mcp/handlers/hunyuan_handler.py:82-83`

No transformation or validation occurs between the MCP parameter and `_import_asset`.

```python
if action == HunyuanAction.IMPORT.value:
    return _import_asset(params.get("zip_url"))
```

**HTTP client receives the caller URL directly** — `blender_mcp/handlers/hunyuan_handler.py:154-170`

`requests.get` resolves and follows the attacker destination from the Blender host before any ZIP check.

```python
def _import_asset(zip_url):
    if not zip_url:
        return {"error": "Zip URL required", "code": "MISSING_ZIP_URL"}
    ...
    r = requests.get(zip_url, stream=True)
    with open(zip_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    ...
    z.extractall(temp_dir)
```

#### Severity

**Medium** — The primitive can reach localhost, private LAN, and metadata-style endpoints from the workstation, but response contents are treated as a ZIP and not returned directly, making exploitation primarily blind.

Raise the rating if a reachable internal GET endpoint causes privileged state change or response-derived data becomes visible to the caller.

Impact assessment:
- **Level:** medium
- **Why:** Internal GET endpoints can be probed or triggered from a trusted network position.

Likelihood assessment:
- **Level:** medium
- **Why:** The request is direct and unrestricted, but useful data extraction is blind and target-dependent.

#### Remediation

Prefer opaque asset IDs whose URL is derived from a trusted service. If arbitrary URLs are unavoidable, require HTTPS, exact host/port allowlists, resolved-IP checks, redirect revalidation, and strict response limits.

Tests:
- Reject loopback, private, link-local, multicast, and reserved resolved addresses.
- Reject a public URL that redirects to a private address.
- Pin or revalidate every redirect and DNS resolution used for the connection.

Preventive controls:
- Centralize outbound URL validation.
- Disable automatic redirects unless each target is validated.
- Record only redacted destination metadata.

<a id="finding-5"></a>

### [5] A timed-out mutation can run later or be replayed after a lost response

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | The source directly shows timeout state overwritten by later queue execution and connection-loss branches resending an uncorrelated command. |
| Category | Improper resource lifecycle and duplicate execution |
| CWE | CWE-664 |
| Affected lines | blender_mcp/core/thread_safety.py:339-347, blender_mcp/core/thread_safety.py:267-275, stdio_bridge.py:104-131, stdio_bridge.py:351-352 |

#### Summary

Queue timeout handling leaves pending commands executable, while the bridge retries ambiguous connection failures without a stable request ID or idempotency ledger. A delete, save, import, or scene edit can therefore run after failure or execute twice.

#### Root Cause

Command lifetime is split across an unsynchronized queue, mutable status, and transient active-task map. Timeout deletes observability without canceling execution. Separately, the bridge omits the JSON-RPC identity from its inner wire command and replays connection-loss failures without checking prior state.

**Timeout removes tracking but not the queued command** — `blender_mcp/core/thread_safety.py:332-347`

The waiter marks `TIMEOUT` and deletes only the lookup entry; the same `cmd` remains in `_task_queue`.

```python
cmd = MCPCommand(func=func, args=args, kwargs=kwargs, tool_id=tool_id, intent=intent)
instance._task_queue.put(cmd)
instance._active_tasks[cmd.id] = cmd
if not cmd.event.wait(timeout):
    cmd.status = ExecutionStatus.TIMEOUT
    del instance._active_tasks[cmd.id]
    raise TimeoutError(f"Execution timed out after {timeout}s")
```

**Queue consumer runs every command regardless of timeout state** — `blender_mcp/core/thread_safety.py:267-275`

A timed-out queued object is later dequeued and executed because there is no atomic pending-state or tombstone check.

```python
while processed < max_per_tick:
    try:
        task = self._task_queue.get_nowait()
    except queue.Empty:
        break
    task.execute()
```

**Execution overwrites the timeout state** — `blender_mcp/core/thread_safety.py:67-78`

`TIMEOUT` is not terminal: late execution rewrites it to `RUNNING` and then a completion state that the caller can no longer query.

```python
def execute(self) -> None:
    self.status = ExecutionStatus.RUNNING
    try:
        self.result = self.func(*self.args, **self.kwargs)
        self.status = ExecutionStatus.COMPLETED
    except Exception as e:
        self.error = e
        self.status = ExecutionStatus.FAILED
    finally:
        self.event.set()
```

**Ambiguous response loss automatically replays the command** — `stdio_bridge.py:104-131`

The first server execution may have committed before the response was lost, but the bridge reconnects and sends the same mutation again.

```python
if response is None:
    if retries > 0:
        self.client_socket = None
        ...
        return self.send_to_blender(command_dict, retries - 1)
...
except (..., OSError, EOFError) as e:
    self.client_socket = None
    if retries > 0:
        ...
        return self.send_to_blender(command_dict, retries - 1)
```

#### Validation

Static tracing established both independently reachable failure modes and confirmed there is no durable request ledger, atomic cancellation transition, or end-to-end idempotency key.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Timeout removes tracking but not the queued command** — `blender_mcp/core/thread_safety.py:332-347`

The waiter marks `TIMEOUT` and deletes only the lookup entry; the same `cmd` remains in `_task_queue`.

```python
cmd = MCPCommand(func=func, args=args, kwargs=kwargs, tool_id=tool_id, intent=intent)
instance._task_queue.put(cmd)
instance._active_tasks[cmd.id] = cmd
if not cmd.event.wait(timeout):
    cmd.status = ExecutionStatus.TIMEOUT
    del instance._active_tasks[cmd.id]
    raise TimeoutError(f"Execution timed out after {timeout}s")
```

**Queue consumer runs every command regardless of timeout state** — `blender_mcp/core/thread_safety.py:267-275`

A timed-out queued object is later dequeued and executed because there is no atomic pending-state or tombstone check.

```python
while processed < max_per_tick:
    try:
        task = self._task_queue.get_nowait()
    except queue.Empty:
        break
    task.execute()
```

**Execution overwrites the timeout state** — `blender_mcp/core/thread_safety.py:67-78`

`TIMEOUT` is not terminal: late execution rewrites it to `RUNNING` and then a completion state that the caller can no longer query.

```python
def execute(self) -> None:
    self.status = ExecutionStatus.RUNNING
    try:
        self.result = self.func(*self.args, **self.kwargs)
        self.status = ExecutionStatus.COMPLETED
    except Exception as e:
        self.error = e
        self.status = ExecutionStatus.FAILED
    finally:
        self.event.set()
```

**Ambiguous response loss automatically replays the command** — `stdio_bridge.py:104-131`

The first server execution may have committed before the response was lost, but the bridge reconnects and sends the same mutation again.

```python
if response is None:
    if retries > 0:
        self.client_socket = None
        ...
        return self.send_to_blender(command_dict, retries - 1)
...
except (..., OSError, EOFError) as e:
    self.client_socket = None
    if retries > 0:
        ...
        return self.send_to_blender(command_dict, retries - 1)
```

Assertions:
- Pending timeout does not remove or tombstone the queue item.
- Late execution overwrites the timeout state.
- Response-loss retries resend a mutation without a stable inner identity.

Counterevidence and remaining uncertainty:
- Healthy requests are processed serially on one connection.
- The explicit 360-second socket timeout branch is not automatically retried, but empty-response and connection-error branches are.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

request/timeout or response loss -\> queue or recursive retry -\> same non-idempotent handler -\> late or duplicate side effect

- **Source:** caller timeout parameters or an ambiguous connection failure

- **Sink:** a non-idempotent Blender mutation such as delete, save, import, or object creation

- **Outcome:** stale or duplicate mutation with an untruthful client-visible result

**Timeout removes tracking but not the queued command** — `blender_mcp/core/thread_safety.py:332-347`

The waiter marks `TIMEOUT` and deletes only the lookup entry; the same `cmd` remains in `_task_queue`.

```python
cmd = MCPCommand(func=func, args=args, kwargs=kwargs, tool_id=tool_id, intent=intent)
instance._task_queue.put(cmd)
instance._active_tasks[cmd.id] = cmd
if not cmd.event.wait(timeout):
    cmd.status = ExecutionStatus.TIMEOUT
    del instance._active_tasks[cmd.id]
    raise TimeoutError(f"Execution timed out after {timeout}s")
```

**Queue consumer runs every command regardless of timeout state** — `blender_mcp/core/thread_safety.py:267-275`

A timed-out queued object is later dequeued and executed because there is no atomic pending-state or tombstone check.

```python
while processed < max_per_tick:
    try:
        task = self._task_queue.get_nowait()
    except queue.Empty:
        break
    task.execute()
```

**Execution overwrites the timeout state** — `blender_mcp/core/thread_safety.py:67-78`

`TIMEOUT` is not terminal: late execution rewrites it to `RUNNING` and then a completion state that the caller can no longer query.

```python
def execute(self) -> None:
    self.status = ExecutionStatus.RUNNING
    try:
        self.result = self.func(*self.args, **self.kwargs)
        self.status = ExecutionStatus.COMPLETED
    except Exception as e:
        self.error = e
        self.status = ExecutionStatus.FAILED
    finally:
        self.event.set()
```

**Ambiguous response loss automatically replays the command** — `stdio_bridge.py:104-131`

The first server execution may have committed before the response was lost, but the bridge reconnects and sends the same mutation again.

```python
if response is None:
    if retries > 0:
        self.client_socket = None
        ...
        return self.send_to_blender(command_dict, retries - 1)
...
except (..., OSError, EOFError) as e:
    self.client_socket = None
    if retries > 0:
        ...
        return self.send_to_blender(command_dict, retries - 1)
```

#### Reachability

Any MCP client can supply `timeout_seconds`; ordinary transport faults or a peer-controlled disconnect can also trigger the retry path.

- **Attacker:** untrusted MCP caller or ordinary client encountering a transport fault

- **Entry point:** dispatcher timeout and bridge `send_to_blender()` retry logic

- **Outcome:** stale or duplicate mutation with an untruthful client-visible result

Preconditions:
- A mutation is delayed in the main-thread queue or begins before its response is lost.

**Timeout removes tracking but not the queued command** — `blender_mcp/core/thread_safety.py:332-347`

The waiter marks `TIMEOUT` and deletes only the lookup entry; the same `cmd` remains in `_task_queue`.

```python
cmd = MCPCommand(func=func, args=args, kwargs=kwargs, tool_id=tool_id, intent=intent)
instance._task_queue.put(cmd)
instance._active_tasks[cmd.id] = cmd
if not cmd.event.wait(timeout):
    cmd.status = ExecutionStatus.TIMEOUT
    del instance._active_tasks[cmd.id]
    raise TimeoutError(f"Execution timed out after {timeout}s")
```

**Queue consumer runs every command regardless of timeout state** — `blender_mcp/core/thread_safety.py:267-275`

A timed-out queued object is later dequeued and executed because there is no atomic pending-state or tombstone check.

```python
while processed < max_per_tick:
    try:
        task = self._task_queue.get_nowait()
    except queue.Empty:
        break
    task.execute()
```

**Execution overwrites the timeout state** — `blender_mcp/core/thread_safety.py:67-78`

`TIMEOUT` is not terminal: late execution rewrites it to `RUNNING` and then a completion state that the caller can no longer query.

```python
def execute(self) -> None:
    self.status = ExecutionStatus.RUNNING
    try:
        self.result = self.func(*self.args, **self.kwargs)
        self.status = ExecutionStatus.COMPLETED
    except Exception as e:
        self.error = e
        self.status = ExecutionStatus.FAILED
    finally:
        self.event.set()
```

**Ambiguous response loss automatically replays the command** — `stdio_bridge.py:104-131`

The first server execution may have committed before the response was lost, but the bridge reconnects and sends the same mutation again.

```python
if response is None:
    if retries > 0:
        self.client_socket = None
        ...
        return self.send_to_blender(command_dict, retries - 1)
...
except (..., OSError, EOFError) as e:
    self.client_socket = None
    if retries > 0:
        ...
        return self.send_to_blender(command_dict, retries - 1)
```

#### Severity

**Medium** — The bug can corrupt valuable scene and `.blend` state, but triggering duplicate execution requires queue delay, response loss, or a selected transport fault. It does not independently cross an OS privilege boundary.

Raise the rating if a deterministic remote path can induce response loss or if a destructive non-idempotent action is shown corrupting an autosaved production asset.

Impact assessment:
- **Level:** high
- **Why:** Repeated or stale writes can corrupt the live scene and protected `.blend` assets.

Likelihood assessment:
- **Level:** medium
- **Why:** A queue delay or ambiguous transport failure is required, though caller-chosen timeouts make the stale-queue case practical.

#### Remediation

Carry one stable request ID from JSON-RPC through the bridge, dispatcher, queue, response, and logs. Maintain a bounded idempotency/result ledger, atomically tombstone pending timeouts, expose running-after-timeout state, and never replay ambiguous mutations before reconciliation.

Tests:
- Force timeout before dequeue and assert the handler never runs.
- Force timeout during execution and assert the returned state is `indeterminate` and later queryable.
- Drop a response after commit and assert retry returns the stored result without rerunning the mutation.

Preventive controls:
- Use a synchronized monotonic command state machine.
- Make mutation idempotency keys mandatory.
- Retain bounded terminal history for reconciliation.

<a id="finding-6"></a>

### [6] Running tools writes code, prompts, paths, and response data to persistent debug logs

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Persistent DEBUG handlers and direct serialization of caller-controlled parameters are explicit in source. |
| Category | Insertion of sensitive information into log files |
| CWE | CWE-532 |
| Affected lines | blender_mcp/dispatcher.py:533-535, blender_mcp/dispatcher.py:685-693, blender_mcp/core/logging_config.py:89-90 |

#### Summary

The dispatcher logs the complete parameter object before redaction, later removes only four exact top-level names, and bridge/server loggers persist raw command and response previews in predictable temporary-directory files.

#### Root Cause

Logging is parameter-centric rather than metadata-allowlisted. One dispatch record bypasses redaction entirely; the later path relies on a shallow denylist and the formatter serializes whatever remains to a persistent DEBUG file.

**Dispatcher attaches the complete parameter object** — `blender_mcp/dispatcher.py:533-535`

Caller-controlled `params`, including `code`, prompts, paths, and URLs, are logged before any sanitizer runs.

```python
logger.debug(
    f"Dispatching command: {tool_name}.{action}",
    extra={"tool": tool_name, "action": action, "params": params, "request_id": request_id},
)
```

**Later redaction is shallow and name-based** — `blender_mcp/dispatcher.py:685-693`

Only four exact top-level names are removed; nested values, case variants, `code`, `prompt`, `secret_key`, paths, and signed URLs remain.

```python
safe_params = {
    k: v for k, v in params.items() if k not in ["password", "api_key", "secret", "token"]
}
logger.log_tool_execution(
    tool=tool or "unknown_tool",
    action=action,
    params=safe_params,
```

**Structured formatter serializes the surviving object** — `blender_mcp/core/logging_config.py:86-92`

The formatter writes parameter and result structures into the DEBUG log; the handler persists them under the system temp directory.

```python
if hasattr(record, "duration_ms"):
    log_data["duration_ms"] = record.duration_ms
if hasattr(record, "params"):
    log_data["params"] = record.params
if hasattr(record, "result"):
    log_data["result"] = record.result
```

#### Validation

The primary reviewer confirmed three independent log families; the structured Blender logger and raw bridge/server previews are enabled at DEBUG and use predictable temp paths.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Dispatcher attaches the complete parameter object** — `blender_mcp/dispatcher.py:533-535`

Caller-controlled `params`, including `code`, prompts, paths, and URLs, are logged before any sanitizer runs.

```python
logger.debug(
    f"Dispatching command: {tool_name}.{action}",
    extra={"tool": tool_name, "action": action, "params": params, "request_id": request_id},
)
```

**Later redaction is shallow and name-based** — `blender_mcp/dispatcher.py:685-693`

Only four exact top-level names are removed; nested values, case variants, `code`, `prompt`, `secret_key`, paths, and signed URLs remain.

```python
safe_params = {
    k: v for k, v in params.items() if k not in ["password", "api_key", "secret", "token"]
}
logger.log_tool_execution(
    tool=tool or "unknown_tool",
    action=action,
    params=safe_params,
```

**Structured formatter serializes the surviving object** — `blender_mcp/core/logging_config.py:86-92`

The formatter writes parameter and result structures into the DEBUG log; the handler persists them under the system temp directory.

```python
if hasattr(record, "duration_ms"):
    log_data["duration_ms"] = record.duration_ms
if hasattr(record, "params"):
    log_data["params"] = record.params
if hasattr(record, "result"):
    log_data["result"] = record.result
```

Assertions:
- Raw params are logged before redaction.
- The later filter is not recursive or value-aware.
- Persistent DEBUG handlers serialize the records.

Counterevidence and remaining uncertainty:
- Four exact top-level secret names are removed in the later record.
- Rotation limits file size but does not sanitize current or retained logs.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

MCP parameters/results -\> dispatcher extras or raw previews -\> DEBUG formatter -\> temp log files -\> later reader

- **Source:** caller-controlled code, prompts, paths, URLs, nested values, and service responses

- **Sink:** rotating and non-rotating debug log files

- **Outcome:** disclosure of sensitive inputs and third-party URL credentials

**Dispatcher attaches the complete parameter object** — `blender_mcp/dispatcher.py:533-535`

Caller-controlled `params`, including `code`, prompts, paths, and URLs, are logged before any sanitizer runs.

```python
logger.debug(
    f"Dispatching command: {tool_name}.{action}",
    extra={"tool": tool_name, "action": action, "params": params, "request_id": request_id},
)
```

**Later redaction is shallow and name-based** — `blender_mcp/dispatcher.py:685-693`

Only four exact top-level names are removed; nested values, case variants, `code`, `prompt`, `secret_key`, paths, and signed URLs remain.

```python
safe_params = {
    k: v for k, v in params.items() if k not in ["password", "api_key", "secret", "token"]
}
logger.log_tool_execution(
    tool=tool or "unknown_tool",
    action=action,
    params=safe_params,
```

**Structured formatter serializes the surviving object** — `blender_mcp/core/logging_config.py:86-92`

The formatter writes parameter and result structures into the DEBUG log; the handler persists them under the system temp directory.

```python
if hasattr(record, "duration_ms"):
    log_data["duration_ms"] = record.duration_ms
if hasattr(record, "params"):
    log_data["params"] = record.params
if hasattr(record, "result"):
    log_data["result"] = record.result
```

#### Reachability

Any caller can place data in logs; exploitation of confidentiality requires local log access, backup access, or collected diagnostics.

- **Attacker:** local process, later workstation user, or support-bundle recipient

- **Entry point:** ordinary MCP tool calls

- **Outcome:** disclosure of sensitive inputs and third-party URL credentials

Preconditions:
- File logging is active in Blender or the bridge/server process.
- The attacker can later read or receive the logs.

**Dispatcher attaches the complete parameter object** — `blender_mcp/dispatcher.py:533-535`

Caller-controlled `params`, including `code`, prompts, paths, and URLs, are logged before any sanitizer runs.

```python
logger.debug(
    f"Dispatching command: {tool_name}.{action}",
    extra={"tool": tool_name, "action": action, "params": params, "request_id": request_id},
)
```

**Later redaction is shallow and name-based** — `blender_mcp/dispatcher.py:685-693`

Only four exact top-level names are removed; nested values, case variants, `code`, `prompt`, `secret_key`, paths, and signed URLs remain.

```python
safe_params = {
    k: v for k, v in params.items() if k not in ["password", "api_key", "secret", "token"]
}
logger.log_tool_execution(
    tool=tool or "unknown_tool",
    action=action,
    params=safe_params,
```

**Structured formatter serializes the surviving object** — `blender_mcp/core/logging_config.py:86-92`

The formatter writes parameter and result structures into the DEBUG log; the handler persists them under the system temp directory.

```python
if hasattr(record, "duration_ms"):
    log_data["duration_ms"] = record.duration_ms
if hasattr(record, "params"):
    log_data["params"] = record.params
if hasattr(record, "result"):
    log_data["result"] = record.result
```

#### Severity

**Medium** — Sensitive code, prompts, paths, signed URLs, and nested secrets can persist and enter support bundles. Reading the files requires local or diagnostic-log access, which keeps the issue below high severity.

Raise the rating if log ACLs permit cross-user reads or production support automatically uploads these logs.

Impact assessment:
- **Level:** medium
- **Why:** Logs may contain arbitrary code, confidential prompts, filesystem paths, and bearer material embedded in URLs.

Likelihood assessment:
- **Level:** medium
- **Why:** Sensitive values naturally occur in normal requests, but a second party must access the files.

#### Remediation

Log only request ID, tool/action, lifecycle state, duration, and coarse outcome. Remove raw command/response previews and parameter/result serialization instead of relying on a denylist.

Tests:
- Send canary values through code, prompt, nested secret, path, and signed-URL fields and assert none appear in any log.
- Assert exception logging redacts request values and headers.
- Verify log permissions are user-only on each supported OS.

Preventive controls:
- Use one centralized metadata allowlist.
- Treat code and prompts as never-log fields.
- Keep structured redaction before every formatter and sink.

<a id="finding-7"></a>

### [7] A local client can exhaust Blender with oversized frames and blocking connections

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | The receive path has no frame-size, client-count, or read-deadline control, and client sockets are explicitly placed in infinite blocking mode. |
| Category | Uncontrolled resource consumption |
| CWE | CWE-400 |
| Affected lines | blender_mcp/core/protocol.py:49-78, blender_mcp/__init__.py:227-256 |

#### Summary

The server creates an uncapped blocking thread per accepted connection and trusts a caller-declared unsigned 32-bit frame length. Partial or oversized loopback frames can consume threads, memory, and repeated buffer-copy time.

#### Root Cause

The protocol allocates work from an unauthenticated length prefix without a maximum, deadline, or bounded connection pool. The server's one-thread-per-client design then turns incomplete frames into indefinitely retained resources.

**Each connection gets an uncapped infinite-blocking thread** — `blender_mcp/__init__.py:227-256`

A peer can hold one thread indefinitely with a partial frame, and the accept loop has no active-client cap.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
...
client.settimeout(None)
```

**Declared frame length has no maximum** — `blender_mcp/core/protocol.py:49-60`

A peer controls the full uint32 `msg_len`, which is passed directly to the blocking body reader before any size check.

```python
raw_len = _recv_n(sock, 4)
if not raw_len:
    return None
msg_len = struct.unpack(">I", raw_len)[0]
msg_bytes = _recv_n(sock, msg_len)
...
return cast(Dict[str, Any], json.loads(msg_bytes.decode("utf-8")))
```

**Body reader repeatedly concatenates until the declared size** — `blender_mcp/core/protocol.py:70-78`

The unbounded body is accumulated with repeated immutable byte-string copies, amplifying memory and CPU cost.

```python
data = b""
while len(data) < n:
    chunk = sock.recv(n - len(data))
    if not chunk:
        return None
    data += chunk
return data
```

#### Validation

No frame-size constant, client semaphore, request byte budget, body deadline, or nesting limit was found on the receive path.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Each connection gets an uncapped infinite-blocking thread** — `blender_mcp/__init__.py:227-256`

A peer can hold one thread indefinitely with a partial frame, and the accept loop has no active-client cap.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
...
client.settimeout(None)
```

**Declared frame length has no maximum** — `blender_mcp/core/protocol.py:49-60`

A peer controls the full uint32 `msg_len`, which is passed directly to the blocking body reader before any size check.

```python
raw_len = _recv_n(sock, 4)
if not raw_len:
    return None
msg_len = struct.unpack(">I", raw_len)[0]
msg_bytes = _recv_n(sock, msg_len)
...
return cast(Dict[str, Any], json.loads(msg_bytes.decode("utf-8")))
```

**Body reader repeatedly concatenates until the declared size** — `blender_mcp/core/protocol.py:70-78`

The unbounded body is accumulated with repeated immutable byte-string copies, amplifying memory and CPU cost.

```python
data = b""
while len(data) < n:
    chunk = sock.recv(n - len(data))
    if not chunk:
        return None
    data += chunk
return data
```

Assertions:
- The full uint32 length is accepted.
- Client reads block indefinitely.
- Accepted connections are not capped.

Counterevidence and remaining uncertainty:
- Loopback limits remote-network exposure.
- `listen(1)` limits the pending backlog, not already accepted handler threads.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

loopback connection -\> per-client thread -\> uint32 length -\> unbounded `_recv_n()` -\> memory/thread exhaustion

- **Source:** attacker-controlled connection count, frame length, and partial body

- **Sink:** unbounded handler threads and body buffering

- **Outcome:** Blender stalls or crashes, risking loss of unsaved work

**Each connection gets an uncapped infinite-blocking thread** — `blender_mcp/__init__.py:227-256`

A peer can hold one thread indefinitely with a partial frame, and the accept loop has no active-client cap.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
...
client.settimeout(None)
```

**Declared frame length has no maximum** — `blender_mcp/core/protocol.py:49-60`

A peer controls the full uint32 `msg_len`, which is passed directly to the blocking body reader before any size check.

```python
raw_len = _recv_n(sock, 4)
if not raw_len:
    return None
msg_len = struct.unpack(">I", raw_len)[0]
msg_bytes = _recv_n(sock, msg_len)
...
return cast(Dict[str, Any], json.loads(msg_bytes.decode("utf-8")))
```

**Body reader repeatedly concatenates until the declared size** — `blender_mcp/core/protocol.py:70-78`

The unbounded body is accumulated with repeated immutable byte-string copies, amplifying memory and CPU cost.

```python
data = b""
while len(data) < n:
    chunk = sock.recv(n - len(data))
    if not chunk:
        return None
    data += chunk
return data
```

#### Reachability

Any local process can trigger the path while the unauthenticated server is running.

- **Attacker:** local process

- **Entry point:** Blender MCP TCP framing protocol

- **Outcome:** Blender stalls or crashes, risking loss of unsaved work

Preconditions:
- The server is running and the loopback port is reachable.

**Each connection gets an uncapped infinite-blocking thread** — `blender_mcp/__init__.py:227-256`

A peer can hold one thread indefinitely with a partial frame, and the accept loop has no active-client cap.

```python
client, address = self.socket.accept()
client_thread = threading.Thread(target=self._handle_client, args=(client,))
client_thread.daemon = True
client_thread.start()
...
client.settimeout(None)
```

**Declared frame length has no maximum** — `blender_mcp/core/protocol.py:49-60`

A peer controls the full uint32 `msg_len`, which is passed directly to the blocking body reader before any size check.

```python
raw_len = _recv_n(sock, 4)
if not raw_len:
    return None
msg_len = struct.unpack(">I", raw_len)[0]
msg_bytes = _recv_n(sock, msg_len)
...
return cast(Dict[str, Any], json.loads(msg_bytes.decode("utf-8")))
```

**Body reader repeatedly concatenates until the declared size** — `blender_mcp/core/protocol.py:70-78`

The unbounded body is accumulated with repeated immutable byte-string copies, amplifying memory and CPU cost.

```python
data = b""
while len(data) < n:
    chunk = sock.recv(n - len(data))
    if not chunk:
        return None
    data += chunk
return data
```

#### Severity

**Medium** — The result is Blender availability loss and possible unsaved-work loss, but the listener is loopback-only and must be running.

Raise the rating if the service is deployed beyond loopback or if a lower-trust sandbox can reach it by default.

Impact assessment:
- **Level:** medium
- **Why:** The attack primarily affects availability and unsaved asset integrity.

Likelihood assessment:
- **Level:** medium
- **Why:** Exploitation is straightforward but local-only by default.

#### Remediation

Reject frame lengths above a small protocol-wide maximum before reading the body, apply handshake/header/body/idle deadlines, cap active clients, and accumulate bytes in a bounded buffer.

Tests:
- Reject a frame one byte above the maximum before reading its body.
- Close partial headers and bodies after a short deadline.
- Hold the configured maximum number of clients and assert the next connection is rejected without a new thread.

Preventive controls:
- Authenticate before parsing action bodies.
- Use bounded worker capacity.
- Cap JSON nesting and collection sizes after byte framing.

<a id="finding-8"></a>

### [8] External downloads can freeze Blender and exhaust memory or disk

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Hunyuan, Poly Haven, and Sketchfab contain directly reachable unbounded request operations, and their handlers are decorated for main-thread execution. |
| Category | Uncontrolled resource consumption |
| CWE | CWE-400 |
| Affected lines | blender_mcp/handlers/hunyuan_handler.py:154-170, blender_mcp/handlers/polyhaven_handler.py:199-271, blender_mcp/handlers/sketchfab_handler.py:145-173 |

#### Summary

External integration handlers perform unbounded HTTP and archive work on Blender's main thread. Requests omit deadlines and byte budgets; some buffer entire responses, and Hunyuan extracts every ZIP member without expansion limits.

#### Root Cause

Network and archive work lacks a shared resource policy. Main-thread integration handlers call `requests` without connect/read/total timeouts, cumulative byte limits, response-size checks, cancellation, or guaranteed temp cleanup; archive extraction adds no decompression budget.

**Hunyuan streams and extracts without total bounds** — `blender_mcp/handlers/hunyuan_handler.py:159-170`

Chunking limits per-iteration memory only; total download, duration, member count, decompressed size, and compression ratio are unbounded.

```python
temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
zip_path = osp.join(temp_dir, "model.zip")
r = requests.get(zip_url, stream=True)
with open(zip_path, "wb") as f:
    for chunk in r.iter_content(8192):
        f.write(chunk)
with zipfile.ZipFile(zip_path, "r") as z:
    z.extractall(temp_dir)
```

**Poly Haven buffers service-selected bodies** — `blender_mcp/handlers/polyhaven_handler.py:223-239`

Both metadata and model responses have no deadlines or size bounds, and `r.content` buffers the entire model before import.

```python
asset_info_url = f"https://api.polyhaven.com/files/{asset_id}"
resp = requests.get(asset_info_url)
data = resp.json()
...
gltf = files.get("gltf", {}).get("glb", {}).get("url")
...
r = requests.get(gltf)
with open(path, "wb") as f:
    f.write(r.content)
...
safe_ops.import_scene.gltf(filepath=path)
```

**Sketchfab also buffers an unbounded model** — `blender_mcp/handlers/sketchfab_handler.py:155-173`

Status checking does not bound time or bytes; the complete response is retained in memory and disk before native import.

```python
gltf_url = gltf_data.get("url")
...
if requests:
    r = requests.get(gltf_url)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    ...
    safe_ops.import_scene.gltf(filepath=path)
```

#### Validation

Review of all four external handlers found no `requests` timeout, download-byte budget, JSON-size limit, ZIP expansion budget, or normal temp-tree cleanup.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Hunyuan streams and extracts without total bounds** — `blender_mcp/handlers/hunyuan_handler.py:159-170`

Chunking limits per-iteration memory only; total download, duration, member count, decompressed size, and compression ratio are unbounded.

```python
temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
zip_path = osp.join(temp_dir, "model.zip")
r = requests.get(zip_url, stream=True)
with open(zip_path, "wb") as f:
    for chunk in r.iter_content(8192):
        f.write(chunk)
with zipfile.ZipFile(zip_path, "r") as z:
    z.extractall(temp_dir)
```

**Poly Haven buffers service-selected bodies** — `blender_mcp/handlers/polyhaven_handler.py:223-239`

Both metadata and model responses have no deadlines or size bounds, and `r.content` buffers the entire model before import.

```python
asset_info_url = f"https://api.polyhaven.com/files/{asset_id}"
resp = requests.get(asset_info_url)
data = resp.json()
...
gltf = files.get("gltf", {}).get("glb", {}).get("url")
...
r = requests.get(gltf)
with open(path, "wb") as f:
    f.write(r.content)
...
safe_ops.import_scene.gltf(filepath=path)
```

**Sketchfab also buffers an unbounded model** — `blender_mcp/handlers/sketchfab_handler.py:155-173`

Status checking does not bound time or bytes; the complete response is retained in memory and disk before native import.

```python
gltf_url = gltf_data.get("url")
...
if requests:
    r = requests.get(gltf_url)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    ...
    safe_ops.import_scene.gltf(filepath=path)
```

Assertions:
- External requests have no finite deadlines.
- Downloaded bodies have no cumulative size budget.
- ZIP extraction has no member or expanded-size limit.

Counterevidence and remaining uncertainty:
- Hunyuan streams compressed input in 8192-byte chunks.
- Sketchfab calls `raise_for_status`; neither control limits total resources.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

external URL/response -\> main-thread `requests` call -\> unbounded buffer/file/archive extraction -\> Blender import

- **Source:** caller-controlled Hunyuan endpoint or external service response bytes

- **Sink:** Blender main thread, process memory, temp disk, and archive extractor

- **Outcome:** UI freeze, process/system exhaustion, and possible loss of unsaved scene work

**Hunyuan streams and extracts without total bounds** — `blender_mcp/handlers/hunyuan_handler.py:159-170`

Chunking limits per-iteration memory only; total download, duration, member count, decompressed size, and compression ratio are unbounded.

```python
temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
zip_path = osp.join(temp_dir, "model.zip")
r = requests.get(zip_url, stream=True)
with open(zip_path, "wb") as f:
    for chunk in r.iter_content(8192):
        f.write(chunk)
with zipfile.ZipFile(zip_path, "r") as z:
    z.extractall(temp_dir)
```

**Poly Haven buffers service-selected bodies** — `blender_mcp/handlers/polyhaven_handler.py:223-239`

Both metadata and model responses have no deadlines or size bounds, and `r.content` buffers the entire model before import.

```python
asset_info_url = f"https://api.polyhaven.com/files/{asset_id}"
resp = requests.get(asset_info_url)
data = resp.json()
...
gltf = files.get("gltf", {}).get("glb", {}).get("url")
...
r = requests.get(gltf)
with open(path, "wb") as f:
    f.write(r.content)
...
safe_ops.import_scene.gltf(filepath=path)
```

**Sketchfab also buffers an unbounded model** — `blender_mcp/handlers/sketchfab_handler.py:155-173`

Status checking does not bound time or bytes; the complete response is retained in memory and disk before native import.

```python
gltf_url = gltf_data.get("url")
...
if requests:
    r = requests.get(gltf_url)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    ...
    safe_ops.import_scene.gltf(filepath=path)
```

#### Reachability

Hunyuan IMPORT accepts a caller URL; other paths require a reachable external provider and, for Sketchfab, a configured key.

- **Attacker:** MCP caller controlling a Hunyuan URL or adversary controlling an external download response

- **Entry point:** external integration IMPORT actions

- **Outcome:** UI freeze, process/system exhaustion, and possible loss of unsaved scene work

Preconditions:
- The relevant integration dependency is installed and the destination is reachable.

**Hunyuan streams and extracts without total bounds** — `blender_mcp/handlers/hunyuan_handler.py:159-170`

Chunking limits per-iteration memory only; total download, duration, member count, decompressed size, and compression ratio are unbounded.

```python
temp_dir = tempfile.mkdtemp(prefix="tencent_obj_")
zip_path = osp.join(temp_dir, "model.zip")
r = requests.get(zip_url, stream=True)
with open(zip_path, "wb") as f:
    for chunk in r.iter_content(8192):
        f.write(chunk)
with zipfile.ZipFile(zip_path, "r") as z:
    z.extractall(temp_dir)
```

**Poly Haven buffers service-selected bodies** — `blender_mcp/handlers/polyhaven_handler.py:223-239`

Both metadata and model responses have no deadlines or size bounds, and `r.content` buffers the entire model before import.

```python
asset_info_url = f"https://api.polyhaven.com/files/{asset_id}"
resp = requests.get(asset_info_url)
data = resp.json()
...
gltf = files.get("gltf", {}).get("glb", {}).get("url")
...
r = requests.get(gltf)
with open(path, "wb") as f:
    f.write(r.content)
...
safe_ops.import_scene.gltf(filepath=path)
```

**Sketchfab also buffers an unbounded model** — `blender_mcp/handlers/sketchfab_handler.py:155-173`

Status checking does not bound time or bytes; the complete response is retained in memory and disk before native import.

```python
gltf_url = gltf_data.get("url")
...
if requests:
    r = requests.get(gltf_url)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    ...
    safe_ops.import_scene.gltf(filepath=path)
```

#### Severity

**Medium** — A controlled endpoint can freeze Blender or fill memory/disk and cause loss of unsaved work. Exploitation requires an enabled/usable integration or a caller-chosen Hunyuan URL.

Raise the rating if a default integration path processes attacker-hosted content without credentials or a demonstrated archive bomb reliably terminates Blender.

Impact assessment:
- **Level:** medium
- **Why:** Resource exhaustion can terminate or freeze Blender and jeopardize unsaved assets.

Likelihood assessment:
- **Level:** medium
- **Why:** Direct Hunyuan control is practical; provider-response paths require provider or CDN influence.

#### Remediation

Move network/archive work off the main thread, apply connect/read/total deadlines and cumulative byte budgets, bound ZIP members and expansion, validate content before import, support cancellation, and always remove temporary trees.

Tests:
- Abort a stalled server within the configured total deadline.
- Reject a response one byte above each integration's maximum.
- Reject ZIPs that exceed member-count, per-member, aggregate, or compression-ratio limits and verify cleanup.

Preventive controls:
- Route all downloads through one bounded helper.
- Use streaming writes with cumulative accounting.
- Keep Blender API import separate from worker-thread network processing.

<a id="finding-9"></a>

### [9] Saving or rendering a scene can copy API credentials into distributable `.blend` files

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Source places secrets on `bpy.types.Scene` without a non-persistent option and saves full scene copies; runtime verification is still recommended for exact serialization and ACL behavior. |
| Category | Cleartext storage of sensitive information |
| CWE | CWE-312 |
| Affected lines | blender_mcp/__init__.py:655-686, blender_mcp/handlers/manage_rendering.py:739-779, blender_mcp/core/job_manager.py:194-215 |

#### Summary

Sketchfab, Hyper3D, and Hunyuan secrets are registered as ordinary `Scene` string properties. UI password masking does not prevent scene serialization, and asynchronous rendering saves complete scene copies to temporary `.blend` files that cleanup paths do not delete.

#### Root Cause

Credential storage uses saved scene data as the secret store. UI masking is mistaken for storage protection, and downstream scene-copy workflows duplicate the same data without a scrub or deletion lifecycle.

**Provider secrets are ordinary Scene properties** — `blender_mcp/__init__.py:655-686`

`PASSWORD` masks display only; the properties have no `SKIP_SAVE` option or external secret reference.

```python
("blendermcp_sketchfab_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hyper3d_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hunyuan3d_secret_id",
 StringProperty(name="Secret ID", default="", subtype="PASSWORD")),
("blendermcp_hunyuan3d_secret_key",
 StringProperty(name="Secret Key", default="", subtype="PASSWORD")),
```

**Credential properties attach to `bpy.types.Scene`** — `blender_mcp/__init__.py:707-712`

Each secret becomes scene data rather than user-scoped keyring or add-on-preference indirection.

```python
for prop_name, prop_value in properties_to_add:
    if hasattr(bpy.types.Scene, prop_name):
        delattr(bpy.types.Scene, prop_name)
    setattr(bpy.types.Scene, prop_name, prop_value)
```

**Async rendering saves a complete scene copy** — `blender_mcp/handlers/manage_rendering.py:739-779`

The full scene, including scene properties, is copied to a retained temp `.blend` file whose path is stored only as job metadata.

```python
temp_filename = f"async_render_{timestamp}_{uuid.uuid4().hex[:8]}.blend"
temp_path = os.path.join(tempfile.gettempdir(), temp_filename)
bpy.ops.wm.save_as_mainfile(filepath=temp_path, copy=True, compress=True)
...
job_id = AsyncJobManager.submit_job(
    command=cmd,
    cwd=tempfile.gettempdir(),
    metadata={"blend_file": temp_path, "scene": scene.name, "output": output_path},
)
```

#### Validation

Static review confirms scene registration, complete scene-copy creation, and cleanup paths that remove only in-memory records or child processes, not the `.blend` file.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Provider secrets are ordinary Scene properties** — `blender_mcp/__init__.py:655-686`

`PASSWORD` masks display only; the properties have no `SKIP_SAVE` option or external secret reference.

```python
("blendermcp_sketchfab_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hyper3d_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hunyuan3d_secret_id",
 StringProperty(name="Secret ID", default="", subtype="PASSWORD")),
("blendermcp_hunyuan3d_secret_key",
 StringProperty(name="Secret Key", default="", subtype="PASSWORD")),
```

**Credential properties attach to `bpy.types.Scene`** — `blender_mcp/__init__.py:707-712`

Each secret becomes scene data rather than user-scoped keyring or add-on-preference indirection.

```python
for prop_name, prop_value in properties_to_add:
    if hasattr(bpy.types.Scene, prop_name):
        delattr(bpy.types.Scene, prop_name)
    setattr(bpy.types.Scene, prop_name, prop_value)
```

**Async rendering saves a complete scene copy** — `blender_mcp/handlers/manage_rendering.py:739-779`

The full scene, including scene properties, is copied to a retained temp `.blend` file whose path is stored only as job metadata.

```python
temp_filename = f"async_render_{timestamp}_{uuid.uuid4().hex[:8]}.blend"
temp_path = os.path.join(tempfile.gettempdir(), temp_filename)
bpy.ops.wm.save_as_mainfile(filepath=temp_path, copy=True, compress=True)
...
job_id = AsyncJobManager.submit_job(
    command=cmd,
    cwd=tempfile.gettempdir(),
    metadata={"blend_file": temp_path, "scene": scene.name, "output": output_path},
)
```

Assertions:
- Secrets are attached to Scene without a non-persistent option.
- Async rendering copies the complete scene.
- No terminal job path deletes the stored `blend_file`.

Counterevidence and remaining uncertainty:
- Credential fields default empty and are masked in the UI.
- Random temp names reduce guessing but not sharing, backup, same-user access, or persistent accumulation.

Limitations:
- Exact Blender RNA serialization and OS temp ACLs were not exercised live; the source-backed storage design should be verified in the supported Blender versions.

#### Dataflow

user credential -\> `StringProperty` on `Scene` -\> `.blend` save/copy -\> shared file or temp reader

- **Source:** user-supplied third-party API credentials

- **Sink:** saved and temporary `.blend` scene data

- **Outcome:** credential disclosure and possible third-party account or quota abuse

**Provider secrets are ordinary Scene properties** — `blender_mcp/__init__.py:655-686`

`PASSWORD` masks display only; the properties have no `SKIP_SAVE` option or external secret reference.

```python
("blendermcp_sketchfab_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hyper3d_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hunyuan3d_secret_id",
 StringProperty(name="Secret ID", default="", subtype="PASSWORD")),
("blendermcp_hunyuan3d_secret_key",
 StringProperty(name="Secret Key", default="", subtype="PASSWORD")),
```

**Credential properties attach to `bpy.types.Scene`** — `blender_mcp/__init__.py:707-712`

Each secret becomes scene data rather than user-scoped keyring or add-on-preference indirection.

```python
for prop_name, prop_value in properties_to_add:
    if hasattr(bpy.types.Scene, prop_name):
        delattr(bpy.types.Scene, prop_name)
    setattr(bpy.types.Scene, prop_name, prop_value)
```

**Async rendering saves a complete scene copy** — `blender_mcp/handlers/manage_rendering.py:739-779`

The full scene, including scene properties, is copied to a retained temp `.blend` file whose path is stored only as job metadata.

```python
temp_filename = f"async_render_{timestamp}_{uuid.uuid4().hex[:8]}.blend"
temp_path = os.path.join(tempfile.gettempdir(), temp_filename)
bpy.ops.wm.save_as_mainfile(filepath=temp_path, copy=True, compress=True)
...
job_id = AsyncJobManager.submit_job(
    command=cmd,
    cwd=tempfile.gettempdir(),
    metadata={"blend_file": temp_path, "scene": scene.name, "output": output_path},
)
```

#### Reachability

The disclosure requires a configured credential plus a saved/shared scene or access to retained temporary render copies.

- **Attacker:** recipient of a `.blend` file or local process that can read retained temp files

- **Entry point:** normal credential configuration followed by save, package, or async render

- **Outcome:** credential disclosure and possible third-party account or quota abuse

Preconditions:
- At least one provider credential is configured.
- A scene is saved/shared or an async render copy is created.

Limitations:
- Exact cross-user readability depends on OS ACLs and was not verified offline.

**Provider secrets are ordinary Scene properties** — `blender_mcp/__init__.py:655-686`

`PASSWORD` masks display only; the properties have no `SKIP_SAVE` option or external secret reference.

```python
("blendermcp_sketchfab_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hyper3d_api_key",
 StringProperty(name="API Key", default="", subtype="PASSWORD")),
...
("blendermcp_hunyuan3d_secret_id",
 StringProperty(name="Secret ID", default="", subtype="PASSWORD")),
("blendermcp_hunyuan3d_secret_key",
 StringProperty(name="Secret Key", default="", subtype="PASSWORD")),
```

**Credential properties attach to `bpy.types.Scene`** — `blender_mcp/__init__.py:707-712`

Each secret becomes scene data rather than user-scoped keyring or add-on-preference indirection.

```python
for prop_name, prop_value in properties_to_add:
    if hasattr(bpy.types.Scene, prop_name):
        delattr(bpy.types.Scene, prop_name)
    setattr(bpy.types.Scene, prop_name, prop_value)
```

**Async rendering saves a complete scene copy** — `blender_mcp/handlers/manage_rendering.py:739-779`

The full scene, including scene properties, is copied to a retained temp `.blend` file whose path is stored only as job metadata.

```python
temp_filename = f"async_render_{timestamp}_{uuid.uuid4().hex[:8]}.blend"
temp_path = os.path.join(tempfile.gettempdir(), temp_filename)
bpy.ops.wm.save_as_mainfile(filepath=temp_path, copy=True, compress=True)
...
job_id = AsyncJobManager.submit_job(
    command=cmd,
    cwd=tempfile.gettempdir(),
    metadata={"blend_file": temp_path, "scene": scene.name, "output": output_path},
)
```

#### Severity

**Medium** — Leaked provider credentials can enable account access or paid API use, but disclosure requires sharing a saved scene, packaging it, or reading retained temp files.

Raise the rating if live Blender validation shows credentials are exposed in common sharing/export workflows or temp-file ACLs allow cross-user access by default.

Impact assessment:
- **Level:** medium
- **Why:** Provider tokens and Tencent secret material can be reused outside Blender.

Likelihood assessment:
- **Level:** medium
- **Why:** Saving and rendering are common, but a second party must obtain the resulting file.

#### Remediation

Move credentials to an OS-backed per-user secret store, keep only opaque references in non-scene preferences, migrate and scrub existing scene values, and delete temporary render copies on every completion, failure, cancellation, eviction, and shutdown path.

Tests:
- Save and inspect a `.blend` after credential configuration and assert no secret bytes are present.
- Run success, failure, cancellation, eviction, and shutdown paths and assert the temporary `.blend` is deleted.
- Warn and migrate when legacy scene credential properties are detected.

Preventive controls:
- Never store credentials on saved Blender ID data.
- Keep secret references separate from project assets.
- Centralize temporary artifact ownership and cleanup.

<a id="finding-10"></a>

### [10] Agent-supplied paths can escape the workspace and overwrite protected files

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Multiple reachable handlers pass caller paths to Blender file sinks after helpers that perform no canonical containment. |
| Category | Path traversal / unrestricted filesystem access |
| CWE | CWE-22, CWE-73 |
| Affected lines | blender_mcp/utils/path_validator.py:28-49, blender_mcp/core/export_pipeline.py:787-839, blender_mcp/handlers/manage_scene.py:120-173 |

#### Summary

Scene I/O and export helpers normalize and create caller-selected paths without an authorized root. The export pipeline also exposes `force_export`, which returns before its blocklist/workspace check.

#### Root Cause

Path helpers are compatibility/normalization utilities, not authorization controls. They create or resolve arbitrary destinations, while the only partial export workspace check can be bypassed directly by a caller parameter and otherwise uses lexical prefix comparison.

**Shared validator resolves and creates but does not confine** — `blender_mcp/utils/path_validator.py:28-49`

The caller path becomes an absolute path and may create directories, but it is never compared to a user-approved root.

```python
if not filepath or not str(filepath).strip():
    raise ValueError(...)
raw_path = Path(str(filepath).strip()).resolve()
...
parent_dir = raw_path.parent
if not parent_dir.exists():
    parent_dir.mkdir(parents=True, exist_ok=True)
return str(raw_path)
```

**Caller-controlled flag disables every export path check** — `blender_mcp/core/export_pipeline.py:787-795`

`force_export` is supplied through the MCP schema, so the agent can skip both the system blocklist and workspace containment logic.

```python
def check_path_injection(filepath: str, force_export: bool = False) -> None:
    ...
    if force_export:
        logger.warning(f"SECURITY BYPASS: force_export=True used for path {filepath}")
        return
```

**Scene save writes to the normalized caller destination** — `blender_mcp/handlers/manage_scene.py:159-173`

A caller-selected destination reaches `save_as_mainfile` without containment or overwrite confirmation.

```python
path = params.get("filepath")
...
if path:
    safe_path = get_safe_path(path)
    safe_ops.wm.save_as_mainfile(filepath=safe_path)
else:
    safe_ops.wm.save_mainfile()
execute_on_main_thread(save_file, timeout=30.0)
```

#### Validation

Source review confirmed unrestricted scene open/save and export paths, a caller-controlled complete bypass, and no central configured read/write root.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Shared validator resolves and creates but does not confine** — `blender_mcp/utils/path_validator.py:28-49`

The caller path becomes an absolute path and may create directories, but it is never compared to a user-approved root.

```python
if not filepath or not str(filepath).strip():
    raise ValueError(...)
raw_path = Path(str(filepath).strip()).resolve()
...
parent_dir = raw_path.parent
if not parent_dir.exists():
    parent_dir.mkdir(parents=True, exist_ok=True)
return str(raw_path)
```

**Caller-controlled flag disables every export path check** — `blender_mcp/core/export_pipeline.py:787-795`

`force_export` is supplied through the MCP schema, so the agent can skip both the system blocklist and workspace containment logic.

```python
def check_path_injection(filepath: str, force_export: bool = False) -> None:
    ...
    if force_export:
        logger.warning(f"SECURITY BYPASS: force_export=True used for path {filepath}")
        return
```

**Scene save writes to the normalized caller destination** — `blender_mcp/handlers/manage_scene.py:159-173`

A caller-selected destination reaches `save_as_mainfile` without containment or overwrite confirmation.

```python
path = params.get("filepath")
...
if path:
    safe_path = get_safe_path(path)
    safe_ops.wm.save_as_mainfile(filepath=safe_path)
else:
    safe_ops.wm.save_mainfile()
execute_on_main_thread(save_file, timeout=30.0)
```

Assertions:
- Final paths are not confined to a user-approved root.
- The MCP caller can request the documented security bypass.
- Scene save can overwrite a caller-selected destination.

Counterevidence and remaining uncertainty:
- Some exporters restrict extensions and OS permissions still apply.
- Several file tools intentionally accept explicit paths, so severity reflects confused-deputy impact rather than privilege escalation.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

MCP `filepath`/`base_path`/`force_export` -\> normalization or bypass -\> Blender open/save/export operator -\> arbitrary accessible path

- **Source:** caller-controlled filesystem path and export bypass flag

- **Sink:** Blender file open, save, export, or render operation

- **Outcome:** overwrite, creation, or loading of files outside the authorized project

**Shared validator resolves and creates but does not confine** — `blender_mcp/utils/path_validator.py:28-49`

The caller path becomes an absolute path and may create directories, but it is never compared to a user-approved root.

```python
if not filepath or not str(filepath).strip():
    raise ValueError(...)
raw_path = Path(str(filepath).strip()).resolve()
...
parent_dir = raw_path.parent
if not parent_dir.exists():
    parent_dir.mkdir(parents=True, exist_ok=True)
return str(raw_path)
```

**Caller-controlled flag disables every export path check** — `blender_mcp/core/export_pipeline.py:787-795`

`force_export` is supplied through the MCP schema, so the agent can skip both the system blocklist and workspace containment logic.

```python
def check_path_injection(filepath: str, force_export: bool = False) -> None:
    ...
    if force_export:
        logger.warning(f"SECURITY BYPASS: force_export=True used for path {filepath}")
        return
```

**Scene save writes to the normalized caller destination** — `blender_mcp/handlers/manage_scene.py:159-173`

A caller-selected destination reaches `save_as_mainfile` without containment or overwrite confirmation.

```python
path = params.get("filepath")
...
if path:
    safe_path = get_safe_path(path)
    safe_ops.wm.save_as_mainfile(filepath=safe_path)
else:
    safe_ops.wm.save_mainfile()
execute_on_main_thread(save_file, timeout=30.0)
```

#### Reachability

Any MCP client permitted to invoke scene or export tools can select the path; Safe Mode currently does not restrict these actions.

- **Attacker:** untrusted or compromised MCP client

- **Entry point:** scene and export tool path parameters

- **Outcome:** overwrite, creation, or loading of files outside the authorized project

Preconditions:
- The Blender process can read or write the target path.

**Shared validator resolves and creates but does not confine** — `blender_mcp/utils/path_validator.py:28-49`

The caller path becomes an absolute path and may create directories, but it is never compared to a user-approved root.

```python
if not filepath or not str(filepath).strip():
    raise ValueError(...)
raw_path = Path(str(filepath).strip()).resolve()
...
parent_dir = raw_path.parent
if not parent_dir.exists():
    parent_dir.mkdir(parents=True, exist_ok=True)
return str(raw_path)
```

**Caller-controlled flag disables every export path check** — `blender_mcp/core/export_pipeline.py:787-795`

`force_export` is supplied through the MCP schema, so the agent can skip both the system blocklist and workspace containment logic.

```python
def check_path_injection(filepath: str, force_export: bool = False) -> None:
    ...
    if force_export:
        logger.warning(f"SECURITY BYPASS: force_export=True used for path {filepath}")
        return
```

**Scene save writes to the normalized caller destination** — `blender_mcp/handlers/manage_scene.py:159-173`

A caller-selected destination reaches `save_as_mainfile` without containment or overwrite confirmation.

```python
path = params.get("filepath")
...
if path:
    safe_path = get_safe_path(path)
    safe_ops.wm.save_as_mainfile(filepath=safe_path)
else:
    safe_ops.wm.save_mainfile()
execute_on_main_thread(save_file, timeout=30.0)
```

#### Severity

**Medium** — The issue exposes unrelated files and valuable `.blend` assets to an MCP caller, but operations run with the user's existing filesystem privileges and some tools intentionally accept output paths.

Raise the rating if a default agent workflow can overwrite high-value files without an explicit user-visible path choice; lower it after all writes are confined to user-approved roots.

Impact assessment:
- **Level:** high
- **Why:** Protected `.blend` files and unrelated writable files can be replaced or corrupted.

Likelihood assessment:
- **Level:** medium
- **Why:** The path is directly caller-controlled, but useful impact depends on filesystem permissions and the selected file operation.

#### Remediation

Define explicit user-approved read and write roots, enforce canonical containment after resolving links/reparse points, remove agent-controlled security bypasses, and require confirmation or exclusive creation before overwrite.

Tests:
- Reject sibling-prefix, traversal, symlink, junction, UNC, and alternate-separator escapes.
- Assert `force_export` cannot be supplied by an MCP caller.
- Assert saving an existing `.blend` outside an approved root requires a local user confirmation.

Preventive controls:
- Separate import and export roots.
- Perform containment before directory creation.
- Use atomic or exclusive file creation when overwrite is not explicitly approved.

<a id="finding-11"></a>

### [11] A crafted batch-selection pattern can freeze Blender's main thread

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | The schema, `re.compile`/`search` sinks, and main-thread decorator are direct source evidence; only the exact runtime duration is environment-dependent. |
| Category | Inefficient regular expression complexity |
| CWE | CWE-1333 |
| Affected lines | blender_mcp/handlers/manage_batch.py:61, blender_mcp/handlers/manage_batch.py:91-104, blender_mcp/handlers/manage_batch.py:621-627 |

#### Summary

`manage_batch` compiles an unrestricted caller regular expression and applies Python's backtracking engine to every object name on Blender's main thread without a complexity or time bound.

#### Root Cause

The batch API exposes general backtracking regular expressions as an untrusted selector while forcing the work onto Blender's main thread. It provides neither a linear-time engine nor pattern/input/time budgets.

**Schema accepts an unrestricted pattern** — `blender_mcp/handlers/manage_batch.py:55-68`

`pattern` has no length or grammar restriction even though nearby numeric inputs are bounded.

```python
"action": ValidationUtils.generate_enum_schema(BatchAction, "Batch operation"),
"selection": {
    "type": "array",
    "items": {"type": "string"},
},
"pattern": {"type": "string", "description": "Regex pattern for name matching"},
...
"duplicate_count": {"type": "integer", "minimum": 1, "maximum": 100},
```

**Backtracking search runs on Blender's main thread** — `blender_mcp/handlers/manage_batch.py:91-104`

The caller expression is compiled by Python's backtracking engine and searched across every object while the handler owns the UI thread.

```python
@ensure_main_thread
@validated_handler(actions=[a.value for a in BatchAction])
def manage_batch(action: str | None = None, **params: Any) -> dict[str, Any]:
    ...
    elif params.get("pattern"):
        pattern = re.compile(params["pattern"])
        targets = [o for o in bpy.data.objects if pattern.search(o.name)]
```

**SELECT_BY_NAME repeats the unbounded search** — `blender_mcp/handlers/manage_batch.py:610-627`

A second independently reachable action applies the same unbounded regex to every object name.

```python
elif action == BatchAction.SELECT_BY_NAME.value:
    pattern_str = params.get("pattern")
    ...
    regex = re.compile(str(pattern_str))
    ...
    for obj in bpy.data.objects:
        if regex.search(obj.name):
```

#### Validation

Static review confirmed unrestricted pattern syntax at both search sites and no worker, timeout, cancellation, pattern-length cap, or object-count budget.

Validation method: Independent static source tracing by the baseline reviewer, focused investigator, and primary reviewer.

**Schema accepts an unrestricted pattern** — `blender_mcp/handlers/manage_batch.py:55-68`

`pattern` has no length or grammar restriction even though nearby numeric inputs are bounded.

```python
"action": ValidationUtils.generate_enum_schema(BatchAction, "Batch operation"),
"selection": {
    "type": "array",
    "items": {"type": "string"},
},
"pattern": {"type": "string", "description": "Regex pattern for name matching"},
...
"duplicate_count": {"type": "integer", "minimum": 1, "maximum": 100},
```

**Backtracking search runs on Blender's main thread** — `blender_mcp/handlers/manage_batch.py:91-104`

The caller expression is compiled by Python's backtracking engine and searched across every object while the handler owns the UI thread.

```python
@ensure_main_thread
@validated_handler(actions=[a.value for a in BatchAction])
def manage_batch(action: str | None = None, **params: Any) -> dict[str, Any]:
    ...
    elif params.get("pattern"):
        pattern = re.compile(params["pattern"])
        targets = [o for o in bpy.data.objects if pattern.search(o.name)]
```

**SELECT_BY_NAME repeats the unbounded search** — `blender_mcp/handlers/manage_batch.py:610-627`

A second independently reachable action applies the same unbounded regex to every object name.

```python
elif action == BatchAction.SELECT_BY_NAME.value:
    pattern_str = params.get("pattern")
    ...
    regex = re.compile(str(pattern_str))
    ...
    for obj in bpy.data.objects:
        if regex.search(obj.name):
```

Assertions:
- The caller controls the regex.
- Python `re.search` is applied repeatedly on the main thread.
- No execution budget or cancellation guard exists.

Counterevidence and remaining uncertainty:
- Invalid syntax raises a structured error.
- Valid catastrophic patterns remain possible, and runtime duration was not measured in this offline scan.

Limitations:
- Application code was not executed during this offline review.

#### Dataflow

MCP `pattern` -\> `re.compile()` -\> repeated `regex.search(object.name)` -\> main-thread CPU exhaustion

- **Source:** caller-controlled regex and scene object names

- **Sink:** Python backtracking regex loop on Blender's main thread

- **Outcome:** extended UI freeze and possible loss of unsaved work after forced termination

**Schema accepts an unrestricted pattern** — `blender_mcp/handlers/manage_batch.py:55-68`

`pattern` has no length or grammar restriction even though nearby numeric inputs are bounded.

```python
"action": ValidationUtils.generate_enum_schema(BatchAction, "Batch operation"),
"selection": {
    "type": "array",
    "items": {"type": "string"},
},
"pattern": {"type": "string", "description": "Regex pattern for name matching"},
...
"duplicate_count": {"type": "integer", "minimum": 1, "maximum": 100},
```

**Backtracking search runs on Blender's main thread** — `blender_mcp/handlers/manage_batch.py:91-104`

The caller expression is compiled by Python's backtracking engine and searched across every object while the handler owns the UI thread.

```python
@ensure_main_thread
@validated_handler(actions=[a.value for a in BatchAction])
def manage_batch(action: str | None = None, **params: Any) -> dict[str, Any]:
    ...
    elif params.get("pattern"):
        pattern = re.compile(params["pattern"])
        targets = [o for o in bpy.data.objects if pattern.search(o.name)]
```

**SELECT_BY_NAME repeats the unbounded search** — `blender_mcp/handlers/manage_batch.py:610-627`

A second independently reachable action applies the same unbounded regex to every object name.

```python
elif action == BatchAction.SELECT_BY_NAME.value:
    pattern_str = params.get("pattern")
    ...
    regex = re.compile(str(pattern_str))
    ...
    for obj in bpy.data.objects:
        if regex.search(obj.name):
```

#### Reachability

Any MCP client can call `manage_batch`; larger scenes amplify the repeated-search cost.

- **Attacker:** untrusted or compromised MCP client

- **Entry point:** `manage_batch` pattern selectors

- **Outcome:** extended UI freeze and possible loss of unsaved work after forced termination

Preconditions:
- A valid high-complexity pattern is supplied.
- At least one matching input drives expensive backtracking.

**Schema accepts an unrestricted pattern** — `blender_mcp/handlers/manage_batch.py:55-68`

`pattern` has no length or grammar restriction even though nearby numeric inputs are bounded.

```python
"action": ValidationUtils.generate_enum_schema(BatchAction, "Batch operation"),
"selection": {
    "type": "array",
    "items": {"type": "string"},
},
"pattern": {"type": "string", "description": "Regex pattern for name matching"},
...
"duplicate_count": {"type": "integer", "minimum": 1, "maximum": 100},
```

**Backtracking search runs on Blender's main thread** — `blender_mcp/handlers/manage_batch.py:91-104`

The caller expression is compiled by Python's backtracking engine and searched across every object while the handler owns the UI thread.

```python
@ensure_main_thread
@validated_handler(actions=[a.value for a in BatchAction])
def manage_batch(action: str | None = None, **params: Any) -> dict[str, Any]:
    ...
    elif params.get("pattern"):
        pattern = re.compile(params["pattern"])
        targets = [o for o in bpy.data.objects if pattern.search(o.name)]
```

**SELECT_BY_NAME repeats the unbounded search** — `blender_mcp/handlers/manage_batch.py:610-627`

A second independently reachable action applies the same unbounded regex to every object name.

```python
elif action == BatchAction.SELECT_BY_NAME.value:
    pattern_str = params.get("pattern")
    ...
    regex = re.compile(str(pattern_str))
    ...
    for obj in bpy.data.objects:
        if regex.search(obj.name):
```

#### Severity

**Medium** — A small request can monopolize the Blender UI and risk unsaved work, but exploit duration depends on crafted pattern/input combinations and the number or shape of object names.

Raise the rating after a reliable short pattern is shown to freeze a typical scene for a material duration; lower it if matching moves to a linear-time engine or strictly bounded worker.

Impact assessment:
- **Level:** medium
- **Why:** The issue affects Blender availability and the integrity of unsaved work.

Likelihood assessment:
- **Level:** medium
- **Why:** Crafted patterns are easy to submit, while exact freeze duration depends on input names and runtime.

#### Remediation

Prefer exact, prefix, suffix, or glob matching. If regex is required, use a linear-time engine or an isolated worker with strict time and input budgets, and keep matching off Blender's main thread.

Tests:
- Reject patterns beyond the documented size/feature subset.
- Run a corpus of catastrophic-backtracking patterns under a strict latency budget.
- Assert matching a maximum-size scene never blocks the Blender main thread.

Preventive controls:
- Use a linear-time selector grammar.
- Cap pattern length and object count.
- Make all potentially unbounded text processing cancellable.

## Reviewed Surfaces

| Surface | Risk Area | Outcome | Notes |
| --- | --- | --- | --- |
| Loopback listener, peer identity, and arbitrary Python | Authentication and code execution | Reported | Two high-severity findings cover missing peer authentication and Safe Mode fail-open authorization. |
| Command queue, timeouts, request correlation, and retries | Mutation integrity | Reported | One medium-severity lifecycle finding covers stale queue execution and ambiguous response-loss replay. |
| TCP framing and client connection management | Availability | Reported | Unsigned frame length and uncapped blocking client threads permit local resource exhaustion. |
| Scene I/O and export path controls | Filesystem integrity | Reported | Path helpers do not enforce authorized roots and `force_export` is caller-controlled. |
| Hunyuan local image and ZIP inputs | Data disclosure and SSRF | Reported | Local file upload and caller-directed network requests survived validation. |
| External HTTP downloads and archive processing | Resource consumption | Reported | Hunyuan, Poly Haven, and Sketchfab lack total deadlines and byte budgets; Hunyuan ZIP expansion is unbounded. |
| Bridge, server, and structured Blender logging | Privacy | Reported | Raw or shallowly redacted parameters and responses are persisted at DEBUG. |
| Third-party credential storage and render copies | Secret storage | Reported | Credentials are scene properties and full async render copies have no file cleanup lifecycle. |
| Batch regex selectors | Main-thread availability | Reported | Two caller-controlled backtracking regex paths lack complexity or time bounds. |
| Provider-response secondary download URLs | SSRF | Rejected | A normal MCP caller cannot independently choose these URLs; exploitation requires compromise of a fixed HTTPS provider/CDN, and provider credentials are not forwarded on secondary downloads. |
| Integration enable toggles | Authorization | Rejected | The toggles behave as UI/status state rather than a source-backed security boundary; concrete network/file consequences are represented by the Hunyuan and external-download findings. |
| Unknown tool bridge validation | Input validation | Rejected | The bridge can forward an uncached tool, but the server dispatcher independently rejects names absent from `HANDLER_REGISTRY`. |
| Telemetry and runtime updates | Privacy and supply chain | No issue found | No telemetry sender, runtime updater, or remote source replacement path was found. The default telemetry preference and unsigned release ZIP remain architecture hardening work. |
| Subprocess command construction | Command injection | No issue found | Reachable render subprocesses use argument lists without `shell=True`; no caller-controlled shell command sink was found. |
| Database, browser, XML, and HTTP-server vulnerability classes | SQL/NoSQL injection, XSS, XXE, request smuggling, redirects, and prototype pollution | Not applicable | The repository has no in-scope database, HTTP server, XML/XPath parser, or browser response surface. |
| Live Blender and operating-system verification | Runtime behavior and platform controls | Needs follow-up | The offline scan could not execute Blender, verify Scene-property serialization/ACLs, or run hostile timeout, frame, archive, and regex regression cases. |
| Candidate validation | Repository security candidates | Reported | Each candidate is source-validated and deduplicated before reporting. |
| Candidate validation | Repository security candidates | Needs follow-up | Each candidate is source-validated and deduplicated before reporting. |
| Candidate validation | Repository security candidates | Needs follow-up | One deduplicated finding is validated; remaining candidates are pending. |
| Independent repository baseline | Cross-repository security controls | Needs follow-up | Candidate discovery completed; validation is next. |
| External integrations and filesystem boundaries | URLs, credentials, downloads, logging, and paths | Needs follow-up | Candidate discovery completed; validation is next. |
| Transport, authorization, and command lifecycle | Authentication, Safe Mode, framing, retries, and timeouts | Needs follow-up | Candidate discovery completed; validation is next. |
| Independent repository baseline | Cross-repository security controls | Needs follow-up | Baseline candidates are awaiting independent validation. |
| External integrations and filesystem boundaries | URLs, credentials, downloads, logging, and paths | Needs follow-up | Focused candidates are awaiting independent validation. |
| Transport, authorization, and command lifecycle | Authentication, Safe Mode, framing, retries, and timeouts | Needs follow-up | Focused validation is still running. |
| External integrations, URL handling, path containment, downloads, credentials, and logging | network and filesystem trust boundaries | Needs follow-up | Focused independent review completed; candidates await parent validation. |

## Open Questions And Follow Up

- Do supported Blender versions serialize all registered Scene credential properties into ordinary and copied `.blend` files?
  - Follow-up prompt: At commit 21e8048ec2e28f974e2d06d937bfd7d18182a52b, run a live Blender test that configures canary Sketchfab and Hunyuan credentials, saves and async-renders the scene, then proves whether either `.blend` contains the canaries and whether every temp file is deleted.
- What are the platform ACLs and cross-user reachability for the loopback listener and temp logs/files?
  - Follow-up prompt: At commit 21e8048ec2e28f974e2d06d937bfd7d18182a52b, test Windows and Linux cross-user access to localhost:9879 plus `%TEMP%` Blender MCP logs and async render copies without modifying repository code.
- Can deterministic hostile inputs reproduce the expected availability failures?
  - Follow-up prompt: Add isolated regression tests for oversized/slow frames, timeout-before-dequeue, response-loss replay, archive expansion budgets, download deadlines, and pathological `manage_batch` regex patterns.
- Run supported Blender versions to confirm credential serialization, temporary-file cleanup and ACLs, and the exact main-thread impact of hostile inputs.
  - Follow-up prompt: Review deferred unit deferred-live-blender and close its stated proof gap.
- `uv` and `pytest` were unavailable in the scan environment, so existing tests and new security regression cases were not executed.
  - Follow-up prompt: Review deferred unit deferred-runtime-tests and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-007 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-002 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-006 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-005 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-001 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-003 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-004 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-ext-008 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-baseline-005 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-baseline-003 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-baseline-004 and close its stated proof gap.
- Awaiting validation or deduplication.
  - Follow-up prompt: Review deferred unit cand-baseline-002 and close its stated proof gap.
- Discovered candidate awaiting independent validation and deduplication.
  - Follow-up prompt: Review deferred unit cand-baseline-001 and close its stated proof gap.
