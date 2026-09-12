# Development Environment

The checked-in development lock was generated on Windows with CPython 3.12.6. It pins the
Foundation 0 toolchain and test dependencies so contributors and automation can reproduce the
validated environment.

## Bootstrap

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

The lock upgrades `pip` itself to the audited version. The editable install is intentionally
separate so the lock never refers to a local checkout path.

## Required checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -q
.\.venv\Scripts\python.exe -m ruff check blender_mcp stdio_bridge.py tests
.\.venv\Scripts\python.exe -m black --check blender_mcp stdio_bridge.py tests
.\.venv\Scripts\python.exe -m mypy blender_mcp stdio_bridge.py
.\.venv\Scripts\python.exe -m pip_audit --local
```

Run Blender-dependent tests only against a disposable profile and disposable `.blend` files. Do
not point an unvalidated branch at valuable assets. The current release gates are tracked in
[`../ROADMAP.md`](../ROADMAP.md), and live validation gaps belong in
[`security/REMEDIATION_LEDGER.md`](security/REMEDIATION_LEDGER.md).

## Windows extension release

The self-contained extension bundles the exact wheels listed in
[`security/WHEEL_INVENTORY.md`](security/WHEEL_INVENTORY.md). Build it through Blender's native
extension command so the manifest and wheel declarations are validated:

```powershell
python create_release_zip.py --blender-executable 'C:\Path\To\blender.exe'
& 'C:\Path\To\blender.exe' --command extension validate .\blender_mcp_v1.0.0.zip
```

Do not update wheel files without updating `SHA256SUMS`, the inventory, locks, dependency audit, and
the disposable installed-extension validation together.

## Updating the lock

Create a clean virtual environment, install `.[dev]`, run the required checks, then regenerate the
lock with:

```powershell
.\.venv\Scripts\python.exe -m pip freeze --exclude-editable
```

Review dependency changes before replacing `requirements-dev.lock`. A Windows lock does not prove
cross-platform compatibility; add and validate per-platform CI locks before widening the supported
release matrix.
