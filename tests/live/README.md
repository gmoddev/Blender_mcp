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
