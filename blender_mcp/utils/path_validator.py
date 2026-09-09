from __future__ import annotations

import os
from typing import Iterable

from ..core.filesystem_boundary import FilesystemAccess, FilesystemPolicyError
from .path import get_safe_path


class PathValidator:
    """
    Utility class to safely handle system paths coming from the MCP LLM Client.
    Protects against Path Traversals and validates file extensions before C-API calls.
    Adheres to Rule 9 (Zero Trust Input).
    """

    @staticmethod
    def validate_and_prepare(
        filepath: str,
        allowed_extensions: Iterable[str] | None = None,
        overwrite: bool = True,
    ) -> str:
        """
        Takes a raw filepath, normalizes it, checks permissions and extension.
        Creates parent directories if missing.

        Args:
            filepath (str): The raw input path from JSON.
            allowed_extensions (set[str]): e.g., {'.glb', '.gltf'}

        Returns:
            str: The safe, absolute, normalized filepath ready for Blender API.

        Raises:
            ValueError: If path is invalid or extension is wrong.
        """
        SafePath = get_safe_path(
            filepath,
            Access=FilesystemAccess.WRITE,
            AllowedExtensions=allowed_extensions,
            CreateParents=True,
        )
        if os.path.exists(SafePath) and not overwrite:
            raise FilesystemPolicyError(
                "FILESYSTEM_OVERWRITE_NOT_REQUESTED",
                "The target exists and this request did not opt in to replacement",
            )
        return SafePath
