# Live Command-Lifecycle Validation

These checks run against a disposable Blender factory session. They do not open, save, or mutate
the user's active `.blend` file.

From the repository root on Windows:

```powershell
.\tests\live\run_lifecycle_validation.ps1 `
  -BlenderPath 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
```

The runner creates a one-run random authentication credential and ephemeral loopback port, starts
the socket suite in a hidden Blender process, terminates only that process, and then runs the
shutdown/timer cleanup check in Blender background mode. It restores any process-level credential
or port environment values before returning.

Covered controls:

- invalid authentication fails closed;
- a pending timeout or cancellation never invokes the mutation;
- a running timeout remains indeterminate until late completion;
- identical request IDs do not re-execute, while content conflicts fail closed;
- a lost socket response can be reconciled after reconnect;
- handler execution stays on Blender's main thread;
- shutdown tombstones queued work and unregisters the persistent timer.

## Filesystem boundary

Run the path-authority checks directly in a disposable background Blender process:

```powershell
& 'C:\Path\To\blender.exe' --background --factory-startup --python-exit-code 1 `
  --python .\tests\live\validate_filesystem_boundary.py
```

The script creates only a uniquely named directory under the OS temporary directory. It checks
outside-root save/open/export denial, sentinel preservation, explicit local overwrite approval, an
approved GLB export, denied sequencer import without editor mutation, approved sequencer image
import, and a Windows junction escape when junction creation is available, then removes the
temporary directory.

## Archive boundary

Run the ZIP and temporary-artifact checks in Blender's embedded Python runtime:

```powershell
& 'C:\Path\To\blender.exe' --background --factory-startup --python-exit-code 1 `
  --python .\tests\live\validate_archive_boundary.py
```

The script creates a unique OS-temporary test root, extracts a bounded archive into an opaque
request workspace, verifies context cleanup, denies traversal before workspace creation, checks
that no outside file appeared, and removes the test root.

## Network boundary

Run the deterministic HTTPS/download helper checks in Blender's embedded Python runtime. The
transport is simulated and makes no external request:

```powershell
& 'C:\Path\To\blender.exe' --background --factory-startup --python-exit-code 1 `
  --python .\tests\live\validate_network_boundary.py
```

The script reauthorizes and resolves an approved redirect, streams a bounded artifact into an
opaque request workspace, closes both connections, denies an oversized body, proves failure
cleanup, and removes the test root.

## Provider job boundary

Run the worker-preparation and main-thread-commit lifecycle in a disposable Blender process:

```powershell
& 'C:\Path\To\blender.exe' --background --factory-startup --python-exit-code 1 `
  --python .\tests\live\validate_provider_jobs.py
```

The script prepares a temporary artifact on a provider worker, commits one canary object only on
Blender's main thread, removes the artifact on a worker, verifies the terminal result, and removes
the canary and temporary root. It performs no external network request.

## Bounded name-selector harness

```powershell
& 'C:\Path\To\blender.exe' --background --factory-startup --python-exit-code 1 `
  --python .\tests\live\validate_name_selectors.py
```

Creates 512 disposable objects, rejects a regex-shaped legacy selector without mutation, exercises
the bounded glob replacement, checks latency budgets, and removes every canary object before exit.

## Installed credential extension

Build and install the extension into a disposable Blender user resource directory, then run
`validate_credential_extension.py`. The script requires `BLENDER_MCP_VALIDATION_OUTPUT` to name a
disposable `.blend`. It uses a unique `gmoddev.BlenderMCP.Validation.*` service, verifies the
WinVault write/read/delete lifecycle, confirms the entry is gone, scrubs a legacy Scene canary,
saves, and proves the canary bytes are absent. It never reads or writes production credential slots.
