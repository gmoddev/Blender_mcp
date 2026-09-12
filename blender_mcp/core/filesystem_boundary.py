"""User-approved filesystem authority for structured MCP actions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Iterable


class FilesystemAccess(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


class FilesystemPolicyError(ValueError):
    """Structured fail-closed filesystem policy error."""

    def __init__(self, Code: str, PublicMessage: str):
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage


@dataclass(frozen=True)
class FilesystemDecision:
    Allowed: bool
    Code: str
    Access: FilesystemAccess
    ResolvedPath: str | None = None
    RootId: str | None = None
    RelativePath: str | None = None
    OverwriteAllowed: bool = False


WindowsReservedNames = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{Index}" for Index in range(1, 10)),
        *(f"LPT{Index}" for Index in range(1, 10)),
    }
)


class FilesystemPolicy:
    """Authorize canonical regular-file paths beneath explicit roots."""

    def __init__(
        self,
        ReadRoot: str | os.PathLike[str] | None = None,
        WriteRoot: str | os.PathLike[str] | None = None,
        AllowOverwrite: bool = False,
    ) -> None:
        self.ReadRoot = self._ResolveRoot(ReadRoot, "read")
        self.WriteRoot = self._ResolveRoot(WriteRoot, "write")
        self.AllowOverwrite = bool(AllowOverwrite)

    def HasRoot(self, Access: FilesystemAccess) -> bool:
        return self._GetRoot(Access) is not None

    def EvaluatePath(
        self,
        Target: str | os.PathLike[str] | None,
        Access: FilesystemAccess,
        AllowedExtensions: Iterable[str] | None = None,
    ) -> FilesystemDecision:
        if not isinstance(Access, FilesystemAccess):
            raise FilesystemPolicyError(
                "INVALID_FILESYSTEM_ACCESS",
                "The filesystem access mode is invalid",
            )
        Root = self._GetRoot(Access)
        RootId = f"{Access.value.lower()}-root"
        if Root is None:
            return self._Deny("FILESYSTEM_ROOT_NOT_CONFIGURED", Access)

        if not isinstance(Target, (str, os.PathLike)):
            return self._Deny("INVALID_FILESYSTEM_PATH", Access)
        RawTarget = os.fspath(Target)
        if not isinstance(RawTarget, str):
            return self._Deny("INVALID_FILESYSTEM_PATH", Access)
        TargetText = RawTarget.strip()
        if not TargetText or "\x00" in TargetText:
            return self._Deny("INVALID_FILESYSTEM_PATH", Access)
        if self._IsNetworkOrDevicePath(TargetText):
            return self._Deny("NETWORK_FILESYSTEM_PATH_DENIED", Access)
        if self._HasUnsafeWindowsComponent(TargetText):
            return self._Deny("UNSAFE_WINDOWS_PATH", Access)

        try:
            TargetPath = Path(TargetText)
            if TargetPath.drive and not TargetPath.is_absolute():
                return self._Deny("DRIVE_RELATIVE_PATH_DENIED", Access)
            Candidate = TargetPath if TargetPath.is_absolute() else Root / TargetPath
            Resolved = Candidate.resolve(strict=Access == FilesystemAccess.READ)
            Relative = Resolved.relative_to(Root)
        except (OSError, RuntimeError, ValueError):
            return self._Deny("FILESYSTEM_PATH_OUTSIDE_ROOT", Access)

        if str(Relative) in ("", "."):
            return self._Deny("REGULAR_FILE_REQUIRED", Access)

        if AllowedExtensions is not None:
            Extensions = {
                Extension.lower() if Extension.startswith(".") else f".{Extension.lower()}"
                for Extension in AllowedExtensions
            }
            if Resolved.suffix.lower() not in Extensions:
                return self._Deny("FILESYSTEM_EXTENSION_DENIED", Access)

        if Access == FilesystemAccess.READ:
            if not Resolved.is_file():
                return self._Deny("READABLE_REGULAR_FILE_REQUIRED", Access)
            if self._HasMultipleHardLinks(Resolved):
                return self._Deny("HARDLINK_PATH_DENIED", Access)
        elif Resolved.exists():
            if not Resolved.is_file():
                return self._Deny("WRITABLE_REGULAR_FILE_REQUIRED", Access)
            if self._HasMultipleHardLinks(Resolved):
                return self._Deny("HARDLINK_PATH_DENIED", Access)
            if not self.AllowOverwrite:
                return self._Deny("FILESYSTEM_OVERWRITE_DENIED", Access)

        return FilesystemDecision(
            Allowed=True,
            Code="FILESYSTEM_PATH_AUTHORIZED",
            Access=Access,
            ResolvedPath=str(Resolved),
            RootId=RootId,
            RelativePath=Relative.as_posix(),
            OverwriteAllowed=Access == FilesystemAccess.WRITE and self.AllowOverwrite,
        )

    def RequirePath(
        self,
        Target: str | os.PathLike[str] | None,
        Access: FilesystemAccess,
        AllowedExtensions: Iterable[str] | None = None,
        CreateParents: bool = False,
    ) -> FilesystemDecision:
        Decision = self.EvaluatePath(Target, Access, AllowedExtensions)
        if not Decision.Allowed or Decision.ResolvedPath is None:
            raise FilesystemPolicyError(Decision.Code, self._GetPublicMessage(Decision.Code))

        if Access == FilesystemAccess.WRITE and CreateParents:
            try:
                Path(Decision.ResolvedPath).parent.mkdir(parents=True, exist_ok=True)
            except OSError as Error:
                raise FilesystemPolicyError(
                    "FILESYSTEM_PARENT_CREATE_FAILED",
                    "The approved output directory could not be created",
                ) from Error
            Decision = self.EvaluatePath(Decision.ResolvedPath, Access, AllowedExtensions)
            if not Decision.Allowed or Decision.ResolvedPath is None:
                raise FilesystemPolicyError(Decision.Code, self._GetPublicMessage(Decision.Code))
        return Decision

    def _GetRoot(self, Access: FilesystemAccess) -> Path | None:
        return self.ReadRoot if Access == FilesystemAccess.READ else self.WriteRoot

    @staticmethod
    def _ResolveRoot(
        RootValue: str | os.PathLike[str] | None,
        RootLabel: str,
    ) -> Path | None:
        if RootValue is None:
            return None
        RawRoot = os.fspath(RootValue)
        if not isinstance(RawRoot, str):
            raise FilesystemPolicyError(
                "FILESYSTEM_ROOT_INVALID",
                f"The configured {RootLabel} root must be a local absolute directory",
            )
        RootText = RawRoot.strip()
        if not RootText:
            return None
        if "\x00" in RootText or FilesystemPolicy._IsNetworkOrDevicePath(RootText):
            raise FilesystemPolicyError(
                "FILESYSTEM_ROOT_INVALID",
                f"The configured {RootLabel} root must be a local absolute directory",
            )
        Root = Path(RootText)
        if not Root.is_absolute():
            raise FilesystemPolicyError(
                "FILESYSTEM_ROOT_INVALID",
                f"The configured {RootLabel} root must be an absolute directory",
            )
        try:
            ResolvedRoot = Root.resolve(strict=True)
        except (OSError, RuntimeError) as Error:
            raise FilesystemPolicyError(
                "FILESYSTEM_ROOT_INVALID",
                f"The configured {RootLabel} root must already exist",
            ) from Error
        if not ResolvedRoot.is_dir():
            raise FilesystemPolicyError(
                "FILESYSTEM_ROOT_INVALID",
                f"The configured {RootLabel} root must be a directory",
            )
        return ResolvedRoot

    @staticmethod
    def _IsNetworkOrDevicePath(PathText: str) -> bool:
        Normalized = PathText.replace("/", "\\")
        return Normalized.startswith("\\\\")

    @staticmethod
    def _HasUnsafeWindowsComponent(PathText: str) -> bool:
        if os.name != "nt":
            return False
        Drive, Tail = os.path.splitdrive(PathText)
        if ":" in Tail:
            return True
        del Drive
        for Component in Tail.replace("/", "\\").split("\\"):
            if not Component or Component in (".", ".."):
                continue
            if Component.endswith((" ", ".")):
                return True
            BaseName = Component.split(".", 1)[0].upper()
            if BaseName in WindowsReservedNames:
                return True
        return False

    @staticmethod
    def _HasMultipleHardLinks(Target: Path) -> bool:
        try:
            return Target.stat().st_nlink > 1
        except OSError:
            return True

    @staticmethod
    def _Deny(Code: str, Access: FilesystemAccess) -> FilesystemDecision:
        return FilesystemDecision(Allowed=False, Code=Code, Access=Access)

    @staticmethod
    def _GetPublicMessage(Code: str) -> str:
        Messages = {
            "FILESYSTEM_ROOT_NOT_CONFIGURED": (
                "This filesystem action is disabled until its user-approved root is configured"
            ),
            "INVALID_FILESYSTEM_ACCESS": "The filesystem access mode is invalid",
            "INVALID_FILESYSTEM_PATH": "A non-empty filesystem path is required",
            "NETWORK_FILESYSTEM_PATH_DENIED": "Network and device filesystem paths are disabled",
            "UNSAFE_WINDOWS_PATH": "The path uses a Windows device, stream, or ambiguous name",
            "DRIVE_RELATIVE_PATH_DENIED": "Drive-relative paths are not accepted",
            "FILESYSTEM_PATH_OUTSIDE_ROOT": "The path is outside the user-approved root",
            "REGULAR_FILE_REQUIRED": "The approved root itself cannot be used as a file",
            "FILESYSTEM_EXTENSION_DENIED": "The file extension is not allowed for this action",
            "READABLE_REGULAR_FILE_REQUIRED": "An existing regular file is required",
            "WRITABLE_REGULAR_FILE_REQUIRED": "The output target must be a regular file",
            "HARDLINK_PATH_DENIED": "Hard-linked files are not accepted at this boundary",
            "FILESYSTEM_OVERWRITE_DENIED": (
                "The target exists and local overwrite approval is disabled"
            ),
            "FILESYSTEM_OVERWRITE_NOT_REQUESTED": (
                "The target exists and this request did not opt in to replacement"
            ),
        }
        return Messages.get(Code, "The filesystem path was denied by local policy")


_PolicyLock = RLock()
_ActivePolicy = FilesystemPolicy()


def ConfigureFilesystemPolicy(
    ReadRoot: str | os.PathLike[str] | None = None,
    WriteRoot: str | os.PathLike[str] | None = None,
    AllowOverwrite: bool = False,
) -> FilesystemPolicy:
    global _ActivePolicy
    Policy = FilesystemPolicy(ReadRoot, WriteRoot, AllowOverwrite)
    with _PolicyLock:
        _ActivePolicy = Policy
    return Policy


def GetFilesystemPolicy() -> FilesystemPolicy:
    with _PolicyLock:
        return _ActivePolicy


def ResetFilesystemPolicy() -> None:
    ConfigureFilesystemPolicy()
