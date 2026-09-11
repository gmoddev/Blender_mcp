"""Capability authorization for Blender MCP."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Iterable


class Capability(str, Enum):
    READ = "READ"
    MUTATE = "MUTATE"
    FILESYSTEM_READ = "FILESYSTEM_READ"
    FILESYSTEM_WRITE = "FILESYSTEM_WRITE"
    NETWORK = "NETWORK"
    EXECUTE_CODE = "EXECUTE_CODE"
    PROCESS = "PROCESS"
    CREDENTIAL_ACCESS = "CREDENTIAL_ACCESS"


@dataclass(frozen=True)
class EffectiveSecurityPolicy:
    """Immutable authorization state safe for socket and worker threads."""

    SafeMode: bool = True
    RawCodeEnabled: bool = False
    Generation: int = 0


_PolicyLock = RLock()
_ActivePolicy = EffectiveSecurityPolicy()


def ConfigureSecurityPolicy(
    SafeMode: bool = True,
    RawCodeEnabled: bool = False,
) -> EffectiveSecurityPolicy:
    """Atomically replace the effective policy without retaining Blender objects."""
    global _ActivePolicy
    EffectiveSafeMode = SafeMode if isinstance(SafeMode, bool) else True
    EffectiveRawCode = RawCodeEnabled if isinstance(RawCodeEnabled, bool) else False
    if EffectiveSafeMode:
        EffectiveRawCode = False
    with _PolicyLock:
        _ActivePolicy = EffectiveSecurityPolicy(
            SafeMode=EffectiveSafeMode,
            RawCodeEnabled=EffectiveRawCode,
            Generation=_ActivePolicy.Generation + 1,
        )
        return _ActivePolicy


def GetSecurityPolicy() -> EffectiveSecurityPolicy:
    """Return the current immutable policy snapshot."""
    with _PolicyLock:
        return _ActivePolicy


def ResetSecurityPolicy() -> None:
    """Restore fail-closed defaults."""
    ConfigureSecurityPolicy()


class SecurityManager:
    """Enforce fail-closed capability policy from effective add-on preferences."""

    @staticmethod
    def is_safe_mode() -> bool:
        """Read effective Safe Mode without touching Blender state."""
        return GetSecurityPolicy().SafeMode

    @staticmethod
    def is_raw_code_enabled() -> bool:
        """Read effective raw-code permission without touching Blender state."""
        return GetSecurityPolicy().RawCodeEnabled

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

        # One immutable snapshot governs the complete decision. Preference changes
        # are published only during main-thread server startup, so a worker cannot
        # observe Safe and Raw fields from different generations.
        SecurityPolicy = GetSecurityPolicy()
        from .filesystem_boundary import FilesystemAccess, GetFilesystemPolicy

        FilesystemPolicy = GetFilesystemPolicy()
        if SecurityPolicy.SafeMode:
            Allowed = {Capability.READ}
            if FilesystemPolicy.HasRoot(FilesystemAccess.READ):
                Allowed.add(Capability.FILESYSTEM_READ)
            return Required.issubset(Allowed)

        Allowed = {Capability.READ, Capability.MUTATE}
        if FilesystemPolicy.HasRoot(FilesystemAccess.READ):
            Allowed.add(Capability.FILESYSTEM_READ)
        if FilesystemPolicy.HasRoot(FilesystemAccess.WRITE):
            Allowed.add(Capability.FILESYSTEM_WRITE)
        if SecurityPolicy.RawCodeEnabled:
            Allowed.add(Capability.EXECUTE_CODE)
        return Required.issubset(Allowed)
