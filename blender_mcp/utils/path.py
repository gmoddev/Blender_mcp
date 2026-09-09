"""Compatibility adapter for the central filesystem authority boundary."""

from __future__ import annotations

import os
from typing import Iterable

from ..core.filesystem_boundary import (
    FilesystemAccess,
    GetFilesystemPolicy,
)


def get_safe_path(
    filepath: str | os.PathLike[str],
    Access: FilesystemAccess | str = FilesystemAccess.WRITE,
    AllowedExtensions: Iterable[str] | None = None,
    CreateParents: bool | None = None,
) -> str:
    """Return the authorized canonical path expected by existing callers."""
    AccessMode = Access if isinstance(Access, FilesystemAccess) else FilesystemAccess(Access)
    ShouldCreateParents = (
        AccessMode == FilesystemAccess.WRITE if CreateParents is None else CreateParents
    )
    Decision = GetFilesystemPolicy().RequirePath(
        filepath,
        AccessMode,
        AllowedExtensions=AllowedExtensions,
        CreateParents=ShouldCreateParents,
    )
    if Decision.ResolvedPath is None:  # Defensive; RequirePath already fails closed.
        raise RuntimeError("Filesystem policy returned no resolved path")
    return Decision.ResolvedPath
