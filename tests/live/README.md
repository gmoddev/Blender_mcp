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
approved GLB export, and a Windows junction escape when junction creation is available, then removes
the temporary directory.
