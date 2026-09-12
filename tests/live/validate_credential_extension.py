"""Validate the installed extension without accessing production credential slots."""

from __future__ import annotations

import importlib
import os
import secrets
import sys
import uuid
from pathlib import Path

import bpy


ValidationSitePackages = os.environ.get("BLENDER_MCP_VALIDATION_SITE_PACKAGES")
if ValidationSitePackages:
    sys.path.insert(0, ValidationSitePackages)
ValidationAddonParent = os.environ.get("BLENDER_MCP_VALIDATION_ADDON_PARENT")
if ValidationAddonParent:
    sys.path.insert(0, ValidationAddonParent)

import keyring  # noqa: E402


AllowedBackend = "keyring.backends.Windows.WinVaultKeyring"
Backend = keyring.get_keyring()
BackendName = f"{type(Backend).__module__}.{type(Backend).__qualname__}"
assert BackendName == AllowedBackend, f"Unexpected credential backend: {BackendName}"

ValidationService = f"gmoddev.BlenderMCP.Validation.{uuid.uuid4()}"
ValidationAccount = "disposable-round-trip"
ValidationValue = secrets.token_urlsafe(32)
try:
    keyring.set_password(ValidationService, ValidationAccount, ValidationValue)
    assert keyring.get_password(ValidationService, ValidationAccount) == ValidationValue
finally:
    try:
        keyring.delete_password(ValidationService, ValidationAccount)
    except Exception:
        pass
assert keyring.get_password(ValidationService, ValidationAccount) is None

AddonModule = os.environ.get("BLENDER_MCP_VALIDATION_ADDON_MODULE", "bl_ext.codex_test.blender_mcp")
Addon = importlib.import_module(AddonModule)
LegacyName = Addon.LegacySceneCredentialProperties[0]
LegacyCanary = f"legacy-scene-canary-{uuid.uuid4()}"
bpy.context.scene[LegacyName] = LegacyCanary
assert Addon.HasLegacySceneCredentials(bpy.context.scene)
assert Addon.PurgeLegacySceneCredentials([bpy.context.scene]) == 1
assert not Addon.HasLegacySceneCredentials(bpy.context.scene)

OutputPath = Path(os.environ["BLENDER_MCP_VALIDATION_OUTPUT"]).resolve()
assert OutputPath.suffix.lower() == ".blend"
bpy.ops.wm.save_as_mainfile(filepath=str(OutputPath), check_existing=False)
assert LegacyCanary.encode("utf-8") not in OutputPath.read_bytes()

print("[BlenderMCP:Credentials] Installed extension validation passed")
