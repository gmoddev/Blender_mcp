"""Capability authorization for Blender MCP."""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, cast

import bpy


class Capability(str, Enum):
    READ = "READ"
    MUTATE = "MUTATE"
    FILESYSTEM_READ = "FILESYSTEM_READ"
    FILESYSTEM_WRITE = "FILESYSTEM_WRITE"
    NETWORK = "NETWORK"
    EXECUTE_CODE = "EXECUTE_CODE"
    PROCESS = "PROCESS"
    CREDENTIAL_ACCESS = "CREDENTIAL_ACCESS"


class SecurityManager:
    """Enforce fail-closed capability policy from effective add-on preferences."""

    @staticmethod
    def is_safe_mode() -> bool:
        """Return effective Safe Mode, defaulting to enabled on lookup failure."""
        try:
            MainPackage = __package__.split(".")[0]
            if bpy.context.preferences and bpy.context.preferences.addons:
                if MainPackage in bpy.context.preferences.addons:
                    Addon = bpy.context.preferences.addons[MainPackage]
                    return bool(cast(Any, Addon).preferences.safe_mode)
            return True
        except (AttributeError, KeyError, IndexError, TypeError):
            return True

    @staticmethod
    def is_raw_code_enabled() -> bool:
        """Return explicit raw-code permission, defaulting to disabled."""
        try:
            MainPackage = __package__.split(".")[0]
            if bpy.context.preferences and bpy.context.preferences.addons:
                if MainPackage in bpy.context.preferences.addons:
                    Addon = bpy.context.preferences.addons[MainPackage]
                    return bool(cast(Any, Addon).preferences.raw_code_enabled)
            return False
        except (AttributeError, KeyError, IndexError, TypeError):
            return False

    @staticmethod
    def validate_action(
        tool_name: str,
        action_name: str,
        capabilities: Iterable[str] | None = None,
    ) -> bool:
        """Authorize one explicitly classified action.

        Existing public naming is preserved. Missing or unknown classifications
        deny. Safe Mode permits only reads. Full Structured Mode permits reads
        and structured mutations. Raw Python additionally requires the separate
        raw-code preference and Safe Mode to be off.
        """
        if not tool_name or not action_name or capabilities is None:
            return False

        try:
            Required = {Capability(Item) for Item in capabilities}
        except (TypeError, ValueError):
            return False
        if not Required:
            return False

        if SecurityManager.is_safe_mode():
            Allowed = {Capability.READ}
            from .filesystem_boundary import FilesystemAccess, GetFilesystemPolicy

            Policy = GetFilesystemPolicy()
            if Policy.HasRoot(FilesystemAccess.READ):
                Allowed.add(Capability.FILESYSTEM_READ)
            return Required.issubset(Allowed)

        Allowed = {Capability.READ, Capability.MUTATE}
        from .filesystem_boundary import FilesystemAccess, GetFilesystemPolicy

        Policy = GetFilesystemPolicy()
        if Policy.HasRoot(FilesystemAccess.READ):
            Allowed.add(Capability.FILESYSTEM_READ)
        if Policy.HasRoot(FilesystemAccess.WRITE):
            Allowed.add(Capability.FILESYSTEM_WRITE)
        if SecurityManager.is_raw_code_enabled():
            Allowed.add(Capability.EXECUTE_CODE)
        return Required.issubset(Allowed)
