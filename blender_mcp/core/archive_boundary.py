"""Bounded extraction for untrusted provider ZIP artifacts."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .filesystem_boundary import WindowsReservedNames


class ArchiveBoundaryError(ValueError):
    """Structured, redacted archive-boundary failure."""

    def __init__(self, Code: str, PublicMessage: str):
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage


@dataclass(frozen=True)
class ArchiveLimits:
    """Resource ceilings applied before and during ZIP extraction."""

    MaxArchiveBytes: int = 256 * 1024 * 1024
    MaxMembers: int = 2048
    MaxMemberBytes: int = 256 * 1024 * 1024
    MaxExpandedBytes: int = 1024 * 1024 * 1024
    MaxCompressionRatio: float = 100.0
    MaxPathCharacters: int = 512
    MaxPathDepth: int = 24
    ChunkBytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        IntegerLimits = (
            self.MaxArchiveBytes,
            self.MaxMembers,
            self.MaxMemberBytes,
            self.MaxExpandedBytes,
            self.MaxPathCharacters,
            self.MaxPathDepth,
            self.ChunkBytes,
        )
        if any(
            not isinstance(Value, int) or isinstance(Value, bool) or Value <= 0
            for Value in IntegerLimits
        ):
            raise ValueError("Archive integer limits must be positive integers")
        if (
            not isinstance(self.MaxCompressionRatio, (int, float))
            or isinstance(self.MaxCompressionRatio, bool)
            or self.MaxCompressionRatio <= 0
        ):
            raise ValueError("Archive compression ratio must be positive")


@dataclass(frozen=True)
class ArchiveMemberPlan:
    RelativePath: str
    IsDirectory: bool
    CompressedBytes: int
    ExpandedBytes: int
    Info: zipfile.ZipInfo


@dataclass(frozen=True)
class ArchivePlan:
    Members: tuple[ArchiveMemberPlan, ...]
    CompressedBytes: int
    ExpandedBytes: int


class ManagedArtifactDirectory:
    """A request-bound workspace whose cleanup is explicit and deterministic."""

    def __init__(self, Root: Path, StoreRoot: Path, RequestId: str):
        self.Root = Root
        self.StoreRoot = StoreRoot
        self.RequestId = RequestId
        self.RelativeFiles: tuple[str, ...] = ()
        self._Cleaned = False

    @property
    def Cleaned(self) -> bool:
        return self._Cleaned

    def Cleanup(self) -> None:
        if self._Cleaned:
            return
        try:
            self.Root.relative_to(self.StoreRoot)
        except ValueError as Error:
            raise ArchiveBoundaryError(
                "ARTIFACT_CLEANUP_BOUNDARY_FAILED",
                "The temporary artifact workspace could not be cleaned safely",
            ) from Error
        try:
            if self.Root.is_symlink():
                self.Root.unlink(missing_ok=True)
            elif _IsJunction(self.Root):
                self.Root.rmdir()
            elif self.Root.exists():
                shutil.rmtree(self.Root)
        except OSError as Error:
            raise ArchiveBoundaryError(
                "ARTIFACT_CLEANUP_FAILED",
                "The temporary artifact workspace could not be cleaned",
            ) from Error
        self._Cleaned = True

    def __enter__(self) -> ManagedArtifactDirectory:
        return self

    def __exit__(self, ExceptionType: object, ExceptionValue: object, Traceback: object) -> None:
        del ExceptionType, ExceptionValue, Traceback
        self.Cleanup()


class ArtifactStore:
    """Create opaque workspaces below one pre-authorized local artifact root."""

    def __init__(self, Root: str | os.PathLike[str]):
        try:
            RootText = os.fspath(Root)
        except TypeError as Error:
            raise ArchiveBoundaryError(
                "ARTIFACT_ROOT_INVALID",
                "The configured temporary artifact root is invalid",
            ) from Error
        if not isinstance(RootText, str) or not RootText.strip() or "\x00" in RootText:
            raise ArchiveBoundaryError(
                "ARTIFACT_ROOT_INVALID",
                "The configured temporary artifact root is invalid",
            )
        if RootText.replace("/", "\\").startswith("\\\\"):
            raise ArchiveBoundaryError(
                "ARTIFACT_ROOT_INVALID",
                "The configured temporary artifact root must be local",
            )
        Candidate = Path(RootText)
        if not Candidate.is_absolute() or Candidate.is_symlink() or _IsJunction(Candidate):
            raise ArchiveBoundaryError(
                "ARTIFACT_ROOT_INVALID",
                "The configured temporary artifact root must be an existing local directory",
            )
        try:
            Resolved = Candidate.resolve(strict=True)
        except (OSError, RuntimeError) as Error:
            raise ArchiveBoundaryError(
                "ARTIFACT_ROOT_INVALID",
                "The configured temporary artifact root must already exist",
            ) from Error
        if not Resolved.is_dir():
            raise ArchiveBoundaryError(
                "ARTIFACT_ROOT_INVALID",
                "The configured temporary artifact root must be a directory",
            )
        self.Root = Resolved

    def CreateWorkspace(self, RequestId: str) -> ManagedArtifactDirectory:
        if not isinstance(RequestId, str) or not RequestId or len(RequestId) > 256:
            raise ArchiveBoundaryError(
                "ARTIFACT_REQUEST_ID_INVALID",
                "A bounded request identity is required for temporary artifacts",
            )
        Workspace: Path | None = None
        try:
            Workspace = Path(tempfile.mkdtemp(prefix="blender-mcp-artifact-", dir=self.Root))
            Workspace.chmod(0o700)
            ResolvedWorkspace = Workspace.resolve(strict=True)
            ResolvedWorkspace.relative_to(self.Root)
        except (OSError, RuntimeError, ValueError) as Error:
            if Workspace is not None and Workspace.parent == self.Root:
                try:
                    if Workspace.is_symlink():
                        Workspace.unlink(missing_ok=True)
                    elif _IsJunction(Workspace):
                        Workspace.rmdir()
                    elif Workspace.exists():
                        shutil.rmtree(Workspace)
                except OSError as CleanupError:
                    raise ArchiveBoundaryError(
                        "ARTIFACT_CLEANUP_FAILED",
                        "The failed temporary artifact workspace could not be cleaned",
                    ) from CleanupError
            raise ArchiveBoundaryError(
                "ARTIFACT_WORKSPACE_CREATE_FAILED",
                "The temporary artifact workspace could not be created",
            ) from Error
        return ManagedArtifactDirectory(ResolvedWorkspace, self.Root, RequestId)


def InspectZipArchive(
    ArchivePath: str | os.PathLike[str],
    Limits: ArchiveLimits | None = None,
) -> ArchivePlan:
    """Validate ZIP metadata without creating output files."""
    EffectiveLimits = Limits or ArchiveLimits()
    PathValue = _RequireArchivePath(ArchivePath, EffectiveLimits)
    try:
        with zipfile.ZipFile(PathValue, "r") as Archive:
            return _InspectOpenArchive(Archive, EffectiveLimits)
    except ArchiveBoundaryError:
        raise
    except Exception as Error:
        raise _Deny("ARCHIVE_INVALID", "The archive is invalid or unsupported") from Error


def ExtractZipArchive(
    ArchivePath: str | os.PathLike[str],
    Store: ArtifactStore,
    RequestId: str,
    Limits: ArchiveLimits | None = None,
) -> ManagedArtifactDirectory:
    """Extract a fully inspected ZIP into a new request-owned workspace."""
    EffectiveLimits = Limits or ArchiveLimits()
    PathValue = _RequireArchivePath(ArchivePath, EffectiveLimits)
    Workspace: ManagedArtifactDirectory | None = None
    try:
        with zipfile.ZipFile(PathValue, "r") as Archive:
            Plan = _InspectOpenArchive(Archive, EffectiveLimits)
            Workspace = Store.CreateWorkspace(RequestId)
            ExpandedTotal = 0
            ExtractedFiles: list[str] = []
            for Member in Plan.Members:
                Target = _GetWorkspaceTarget(Workspace, Member.RelativePath)
                if Member.IsDirectory:
                    Target.mkdir(parents=True, exist_ok=True)
                    _VerifyWorkspaceParent(Workspace, Target)
                    if not Target.is_dir():
                        raise _Deny(
                            "ARCHIVE_PATH_COLLISION",
                            "The archive contains colliding member paths",
                        )
                    continue
                Target.parent.mkdir(parents=True, exist_ok=True)
                _VerifyWorkspaceParent(Workspace, Target.parent)
                MemberBytes = 0
                with Archive.open(Member.Info, "r") as Source, Target.open("xb") as Destination:
                    while True:
                        Chunk = Source.read(EffectiveLimits.ChunkBytes)
                        if not Chunk:
                            break
                        MemberBytes += len(Chunk)
                        ExpandedTotal += len(Chunk)
                        if (
                            MemberBytes > Member.ExpandedBytes
                            or MemberBytes > EffectiveLimits.MaxMemberBytes
                        ):
                            raise _Deny(
                                "ARCHIVE_MEMBER_SIZE_MISMATCH",
                                "An archive member exceeded its declared size",
                            )
                        if ExpandedTotal > EffectiveLimits.MaxExpandedBytes:
                            raise _Deny(
                                "ARCHIVE_EXPANDED_LIMIT_EXCEEDED",
                                "The archive exceeded its expanded size limit",
                            )
                        Destination.write(Chunk)
                if MemberBytes != Member.ExpandedBytes:
                    raise _Deny(
                        "ARCHIVE_MEMBER_SIZE_MISMATCH",
                        "An archive member did not match its declared size",
                    )
                ExtractedFiles.append(Member.RelativePath)
            Workspace.RelativeFiles = tuple(ExtractedFiles)
            return Workspace
    except ArchiveBoundaryError:
        if Workspace is not None:
            Workspace.Cleanup()
        raise
    except Exception as Error:
        if Workspace is not None:
            Workspace.Cleanup()
        raise _Deny(
            "ARCHIVE_EXTRACTION_FAILED", "The archive could not be extracted safely"
        ) from Error


def _RequireArchivePath(ArchivePath: str | os.PathLike[str], Limits: ArchiveLimits) -> Path:
    try:
        RawPath = os.fspath(ArchivePath)
    except TypeError as Error:
        raise _Deny("ARCHIVE_PATH_INVALID", "An existing ZIP archive is required") from Error
    if not isinstance(RawPath, str) or not RawPath.strip() or "\x00" in RawPath:
        raise _Deny("ARCHIVE_PATH_INVALID", "An existing ZIP archive is required")
    Candidate = Path(RawPath)
    if Candidate.is_symlink() or _IsJunction(Candidate):
        raise _Deny("ARCHIVE_LINK_DENIED", "Linked archive files are not accepted")
    try:
        Resolved = Candidate.resolve(strict=True)
        Metadata = Resolved.stat()
    except (OSError, RuntimeError) as Error:
        raise _Deny("ARCHIVE_PATH_INVALID", "An existing ZIP archive is required") from Error
    if not Resolved.is_file():
        raise _Deny("ARCHIVE_PATH_INVALID", "An existing ZIP archive is required")
    if Metadata.st_nlink > 1:
        raise _Deny("ARCHIVE_HARDLINK_DENIED", "Hard-linked archive files are not accepted")
    if Metadata.st_size > Limits.MaxArchiveBytes:
        raise _Deny(
            "ARCHIVE_COMPRESSED_LIMIT_EXCEEDED", "The archive exceeded its compressed size limit"
        )
    if Resolved.suffix.lower() != ".zip":
        raise _Deny("ARCHIVE_TYPE_DENIED", "Only ZIP archives are accepted")
    return Resolved


def _InspectOpenArchive(Archive: zipfile.ZipFile, Limits: ArchiveLimits) -> ArchivePlan:
    Members = Archive.infolist()
    if not Members:
        raise _Deny("ARCHIVE_EMPTY", "The archive contains no files")
    if len(Members) > Limits.MaxMembers:
        raise _Deny("ARCHIVE_MEMBER_LIMIT_EXCEEDED", "The archive contains too many members")
    return _BuildPlan(Members, Limits)


def _BuildPlan(Members: list[zipfile.ZipInfo], Limits: ArchiveLimits) -> ArchivePlan:
    Planned: list[ArchiveMemberPlan] = []
    ExplicitKeys: set[str] = set()
    FileKeys: set[str] = set()
    CompressedTotal = 0
    ExpandedTotal = 0
    for Info in Members:
        RelativePath = _ValidateMemberName(Info.filename, Limits)
        IsDirectory = Info.is_dir()
        _ValidateMemberType(Info, IsDirectory)
        if Info.flag_bits & 0x1:
            raise _Deny("ARCHIVE_ENCRYPTION_DENIED", "Encrypted archive members are not accepted")
        if Info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise _Deny(
                "ARCHIVE_COMPRESSION_DENIED",
                "The archive uses an unsupported compression method",
            )
        if Info.compress_size < 0 or Info.file_size < 0:
            raise _Deny("ARCHIVE_INVALID_SIZE", "The archive contains invalid size metadata")
        if Info.file_size > Limits.MaxMemberBytes:
            raise _Deny("ARCHIVE_MEMBER_LIMIT_EXCEEDED", "An archive member is too large")
        Ratio = Info.file_size / max(Info.compress_size, 1)
        if Ratio > Limits.MaxCompressionRatio:
            raise _Deny(
                "ARCHIVE_COMPRESSION_RATIO_EXCEEDED",
                "An archive member exceeded the compression-ratio limit",
            )
        CompressedTotal += Info.compress_size
        ExpandedTotal += Info.file_size
        if CompressedTotal > Limits.MaxArchiveBytes:
            raise _Deny(
                "ARCHIVE_COMPRESSED_LIMIT_EXCEEDED",
                "The archive exceeded its compressed size limit",
            )
        if ExpandedTotal > Limits.MaxExpandedBytes:
            raise _Deny(
                "ARCHIVE_EXPANDED_LIMIT_EXCEEDED", "The archive exceeded its expanded size limit"
            )

        Key = _CollisionKey(RelativePath)
        if Key in ExplicitKeys:
            raise _Deny("ARCHIVE_PATH_COLLISION", "The archive contains colliding member paths")
        Parts = Key.split("/")
        Ancestors = {"/".join(Parts[:Index]) for Index in range(1, len(Parts))}
        if Ancestors & FileKeys:
            raise _Deny("ARCHIVE_PATH_COLLISION", "The archive contains colliding member paths")
        if not IsDirectory and any(Value.startswith(f"{Key}/") for Value in ExplicitKeys):
            raise _Deny("ARCHIVE_PATH_COLLISION", "The archive contains colliding member paths")
        ExplicitKeys.add(Key)
        if not IsDirectory:
            FileKeys.add(Key)
        Planned.append(
            ArchiveMemberPlan(
                RelativePath=RelativePath,
                IsDirectory=IsDirectory,
                CompressedBytes=Info.compress_size,
                ExpandedBytes=Info.file_size,
                Info=Info,
            )
        )
    if not any(not Member.IsDirectory for Member in Planned):
        raise _Deny("ARCHIVE_EMPTY", "The archive contains no files")
    return ArchivePlan(tuple(Planned), CompressedTotal, ExpandedTotal)


def _ValidateMemberName(Name: str, Limits: ArchiveLimits) -> str:
    if not isinstance(Name, str) or not Name or "\x00" in Name or "\\" in Name:
        raise _Deny("ARCHIVE_MEMBER_PATH_DENIED", "The archive contains an unsafe member path")
    Trimmed = Name[:-1] if Name.endswith("/") else Name
    if not Trimmed or Name.startswith(("/", "//")) or len(Trimmed) > Limits.MaxPathCharacters:
        raise _Deny("ARCHIVE_MEMBER_PATH_DENIED", "The archive contains an unsafe member path")
    RawParts = Trimmed.split("/")
    if (
        not RawParts
        or len(RawParts) > Limits.MaxPathDepth
        or any(Part in ("", ".", "..") for Part in RawParts)
    ):
        raise _Deny("ARCHIVE_MEMBER_PATH_DENIED", "The archive contains an unsafe member path")
    Parts = tuple(RawParts)
    for Part in Parts:
        if (
            ":" in Part
            or Part.endswith((" ", "."))
            or any(ord(Character) < 32 for Character in Part)
            or Part.split(".", 1)[0].upper() in WindowsReservedNames
        ):
            raise _Deny("ARCHIVE_MEMBER_PATH_DENIED", "The archive contains an unsafe member path")
    return "/".join(Parts)


def _ValidateMemberType(Info: zipfile.ZipInfo, IsDirectory: bool) -> None:
    UnixMode = (Info.external_attr >> 16) & 0xFFFF
    FileType = stat.S_IFMT(UnixMode)
    if FileType == 0:
        return
    ExpectedType = stat.S_IFDIR if IsDirectory else stat.S_IFREG
    if FileType != ExpectedType:
        raise _Deny("ARCHIVE_MEMBER_TYPE_DENIED", "The archive contains a link or special member")


def _CollisionKey(RelativePath: str) -> str:
    return "/".join(
        unicodedata.normalize("NFC", Part).casefold() for Part in RelativePath.split("/")
    )


def _GetWorkspaceTarget(Workspace: ManagedArtifactDirectory, RelativePath: str) -> Path:
    Target = Workspace.Root.joinpath(*RelativePath.split("/"))
    try:
        Target.resolve(strict=False).relative_to(Workspace.Root)
    except (OSError, RuntimeError, ValueError) as Error:
        raise _Deny(
            "ARCHIVE_EXTRACTION_BOUNDARY_FAILED", "An archive member escaped its workspace"
        ) from Error
    return Target


def _VerifyWorkspaceParent(Workspace: ManagedArtifactDirectory, Target: Path) -> None:
    try:
        Resolved = Target.resolve(strict=True)
        Resolved.relative_to(Workspace.Root)
    except (OSError, RuntimeError, ValueError) as Error:
        raise _Deny(
            "ARCHIVE_EXTRACTION_BOUNDARY_FAILED", "An archive member escaped its workspace"
        ) from Error


def _IsJunction(PathValue: Path) -> bool:
    IsJunction = getattr(PathValue, "is_junction", None)
    return bool(IsJunction and IsJunction())


def _Deny(Code: str, PublicMessage: str) -> ArchiveBoundaryError:
    return ArchiveBoundaryError(Code, PublicMessage)
