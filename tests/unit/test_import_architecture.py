"""Tests for the source-layer import architecture gate."""

import ast
from pathlib import Path

from scripts.quality.lint_imports import ImportVisitor


def CheckImport(FilePath: str, Statement: str) -> list[str]:
    Visitor = ImportVisitor(Path(FilePath))
    Visitor.visit(ast.parse(Statement))
    return Visitor.errors


def test_ordinary_utils_module_cannot_import_core() -> None:
    Errors = CheckImport("blender_mcp/utils/example.py", "from ..core.security import Capability")

    assert Errors == ["Line 1: Utils module importing Core layer '..core.security'"]


def test_exact_filesystem_compatibility_adapters_can_delegate_to_core() -> None:
    for FilePath in (
        "blender_mcp/utils/path.py",
        "blender_mcp/utils/path_validator.py",
    ):
        assert (
            CheckImport(FilePath, "from ..core.filesystem_boundary import FilesystemAccess") == []
        )


def test_compatibility_adapter_cannot_import_handlers() -> None:
    Errors = CheckImport(
        "blender_mcp/utils/path.py", "from ..handlers.manage_scene import manage_scene"
    )

    assert Errors == ["Line 1: Utils module importing Handler layer '..handlers.manage_scene'"]
