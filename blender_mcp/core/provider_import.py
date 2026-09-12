"""Transactional main-thread Blender import for prepared provider GLB artifacts."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .archive_boundary import ManagedArtifactDirectory
from .provider_content import (
    ProviderContentError,
    ProviderContentLimits,
    ProviderGlbPlan,
    ProviderImportLimits,
    ProviderImportSnapshot,
    RevalidateProviderGlb,
    ValidateProviderImportOutcome,
)
from .provider_jobs import ProviderCommitError, ProviderCommitOutcome


TrackedDataCollections = (
    "objects",
    "collections",
    "actions",
    "armatures",
    "cameras",
    "lights",
    "curves",
    "meshes",
    "materials",
    "node_groups",
    "images",
)

RollbackOrder = TrackedDataCollections
ClockFunction = Callable[[], float]


@dataclass(frozen=True)
class ProviderNativeImportLimits:
    MaxElapsedSeconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.MaxElapsedSeconds, (int, float))
            or isinstance(self.MaxElapsedSeconds, bool)
            or not math.isfinite(self.MaxElapsedSeconds)
            or not 0 < self.MaxElapsedSeconds <= 300
        ):
            raise ValueError("Provider native import deadline must be positive and bounded")


class ProviderImportCommitError(ProviderCommitError):
    """Redacted provider import failure with explicit rollback truth."""

    def __init__(self, Code: str, PublicMessage: str, RollbackComplete: bool):
        super().__init__(Code, PublicMessage)
        self.RollbackComplete = RollbackComplete


class ProviderImportBackend(Protocol):
    def CaptureState(self) -> object: ...

    def Snapshot(self) -> ProviderImportSnapshot: ...

    def ImportGlb(self, PathValue: Path) -> bool: ...

    def RestoreContext(self, State: object) -> None: ...

    def Rollback(self, State: object) -> None: ...

    def MatchesState(self, State: object) -> bool: ...


@dataclass(frozen=True)
class _BlenderImportState:
    DataIds: dict[str, frozenset[int]]
    SelectedObjectIds: frozenset[int]
    ActiveObjectId: int | None


class BlenderProviderImportBackend:
    """Small adapter around the Blender API; instantiate only on Blender's main thread."""

    def __init__(self, BpyModule: Any | None = None):
        if BpyModule is None:
            try:
                import bpy as ImportedBpy
            except ImportError as Error:
                raise ProviderImportCommitError(
                    "PROVIDER_IMPORT_UNAVAILABLE",
                    "The Blender importer is unavailable",
                    True,
                ) from Error
            BpyModule = ImportedBpy
        self._Bpy = BpyModule

    def CaptureState(self) -> object:
        if self._Bpy.context.mode != "OBJECT":
            raise ProviderImportCommitError(
                "PROVIDER_IMPORT_CONTEXT_DENIED",
                "Provider imports require Blender Object Mode",
                True,
            )
        DataIds = {
            Name: frozenset(_Identity(Item) for Item in getattr(self._Bpy.data, Name))
            for Name in TrackedDataCollections
        }
        Selected = frozenset(_Identity(Item) for Item in self._Bpy.context.selected_objects)
        Active = self._Bpy.context.view_layer.objects.active
        return _BlenderImportState(
            DataIds=DataIds,
            SelectedObjectIds=Selected,
            ActiveObjectId=_Identity(Active) if Active is not None else None,
        )

    def Snapshot(self) -> ProviderImportSnapshot:
        return ProviderImportSnapshot(
            Objects=len(self._Bpy.data.objects),
            Meshes=len(self._Bpy.data.meshes),
            Materials=len(self._Bpy.data.materials),
            Images=len(self._Bpy.data.images),
            Armatures=len(self._Bpy.data.armatures),
            Vertices=sum(len(Mesh.vertices) for Mesh in self._Bpy.data.meshes),
            Polygons=sum(len(Mesh.polygons) for Mesh in self._Bpy.data.meshes),
        )

    def ImportGlb(self, PathValue: Path) -> bool:
        Result = self._Bpy.ops.import_scene.gltf(filepath=str(PathValue))
        return "FINISHED" in Result

    def RestoreContext(self, State: object) -> None:
        Captured = _RequireState(State)
        Active = None
        for Item in self._Bpy.data.objects:
            ItemId = _Identity(Item)
            Item.select_set(ItemId in Captured.SelectedObjectIds)
            if ItemId == Captured.ActiveObjectId:
                Active = Item
        self._Bpy.context.view_layer.objects.active = Active

    def Rollback(self, State: object) -> None:
        Captured = _RequireState(State)
        for Name in RollbackOrder:
            Collection = getattr(self._Bpy.data, Name)
            OriginalIds = Captured.DataIds[Name]
            for Item in tuple(Collection):
                if _Identity(Item) not in OriginalIds:
                    Collection.remove(Item, do_unlink=True)
        self.RestoreContext(Captured)

    def MatchesState(self, State: object) -> bool:
        Captured = _RequireState(State)
        for Name in TrackedDataCollections:
            Current = frozenset(_Identity(Item) for Item in getattr(self._Bpy.data, Name))
            if Current != Captured.DataIds[Name]:
                return False
        Selected = frozenset(_Identity(Item) for Item in self._Bpy.context.selected_objects)
        Active = self._Bpy.context.view_layer.objects.active
        ActiveId = _Identity(Active) if Active is not None else None
        return Selected == Captured.SelectedObjectIds and ActiveId == Captured.ActiveObjectId


def CommitProviderGlb(
    Workspace: ManagedArtifactDirectory,
    Plan: ProviderGlbPlan,
    ContentLimits: ProviderContentLimits | None = None,
    ImportLimits: ProviderImportLimits | None = None,
    NativeLimits: ProviderNativeImportLimits | None = None,
    Backend: ProviderImportBackend | None = None,
    Clock: ClockFunction | None = None,
) -> ProviderCommitOutcome:
    """Revalidate, import, verify, and roll back a provider GLB as one commit attempt."""
    _RequireMainThread()
    EffectiveLimits = NativeLimits or ProviderNativeImportLimits()
    EffectiveClock = Clock or time.monotonic
    if not callable(EffectiveClock):
        raise ValueError("Provider native import requires a monotonic clock")
    try:
        RevalidateProviderGlb(Workspace, Plan, ContentLimits)
    except ProviderContentError as Error:
        raise ProviderImportCommitError(Error.Code, Error.PublicMessage, True) from Error
    EffectiveBackend = Backend or BlenderProviderImportBackend()
    try:
        State = EffectiveBackend.CaptureState()
        Before = EffectiveBackend.Snapshot()
        StartedAt = _ReadClock(EffectiveClock)
    except ProviderImportCommitError as Error:
        raise ProviderImportCommitError(Error.Code, Error.PublicMessage, True) from Error
    except Exception as Error:
        raise ProviderImportCommitError(
            "PROVIDER_IMPORT_PREFLIGHT_FAILED",
            "The provider import could not capture a safe baseline",
            True,
        ) from Error

    try:
        if not EffectiveBackend.ImportGlb(Plan.Path):
            raise ProviderImportCommitError(
                "PROVIDER_IMPORT_OPERATOR_FAILED",
                "The provider model importer did not finish",
                False,
            )
        Elapsed = _ReadClock(EffectiveClock) - StartedAt
        if Elapsed < 0:
            raise ProviderImportCommitError(
                "PROVIDER_IMPORT_CLOCK_INVALID",
                "The provider import clock was invalid",
                False,
            )
        if Elapsed > EffectiveLimits.MaxElapsedSeconds:
            raise ProviderImportCommitError(
                "PROVIDER_IMPORT_DEADLINE_EXCEEDED",
                "The provider import exceeded its completion deadline",
                False,
            )
        Delta = ValidateProviderImportOutcome(Before, EffectiveBackend.Snapshot(), ImportLimits)
        EffectiveBackend.RestoreContext(State)
    except Exception as Error:
        _RollbackOrRaise(EffectiveBackend, State, Error)
        raise AssertionError("unreachable")
    return ProviderCommitOutcome("PROVIDER_GLB_IMPORTED", Delta.Objects)


def _RollbackOrRaise(
    Backend: ProviderImportBackend,
    State: object,
    OriginalError: Exception,
) -> None:
    try:
        Backend.Rollback(State)
        if not Backend.MatchesState(State):
            raise RuntimeError("state mismatch")
    except Exception as RollbackError:
        raise ProviderImportCommitError(
            "PROVIDER_IMPORT_ROLLBACK_FAILED",
            "The provider import failed and rollback could not be verified",
            False,
        ) from RollbackError
    if isinstance(OriginalError, ProviderImportCommitError):
        raise ProviderImportCommitError(
            OriginalError.Code,
            OriginalError.PublicMessage,
            True,
        ) from OriginalError
    if isinstance(OriginalError, ProviderContentError):
        raise ProviderImportCommitError(
            OriginalError.Code,
            OriginalError.PublicMessage,
            True,
        ) from OriginalError
    raise ProviderImportCommitError(
        "PROVIDER_IMPORT_FAILED",
        "The provider model could not be imported",
        True,
    ) from OriginalError


def _RequireMainThread() -> None:
    MainThreadId = threading.main_thread().ident
    if MainThreadId is None or threading.get_ident() != MainThreadId:
        raise ProviderImportCommitError(
            "PROVIDER_IMPORT_MAIN_THREAD_REQUIRED",
            "Provider imports require Blender's main thread",
            True,
        )


def _ReadClock(Clock: ClockFunction) -> float:
    Value = Clock()
    if not isinstance(Value, (int, float)) or isinstance(Value, bool) or not math.isfinite(Value):
        raise ProviderImportCommitError(
            "PROVIDER_IMPORT_CLOCK_INVALID",
            "The provider import clock was invalid",
            False,
        )
    return float(Value)


def _Identity(Item: Any) -> int:
    Value = Item.as_pointer()
    if not isinstance(Value, int) or isinstance(Value, bool) or Value <= 0:
        raise RuntimeError("invalid Blender datablock identity")
    return Value


def _RequireState(State: object) -> _BlenderImportState:
    if not isinstance(State, _BlenderImportState):
        raise TypeError("invalid provider import state")
    return State
