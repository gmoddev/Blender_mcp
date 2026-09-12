"""Tests for the source-layer import architecture gate."""

import ast
from pathlib import Path

from scripts.quality.lint_imports import GetSourceFiles, ImportVisitor


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


def test_generated_installed_copy_is_not_scanned(tmp_path: Path) -> None:
    Source = tmp_path / "blender_mcp" / "core" / "source.py"
    InstalledCopy = tmp_path / ".pytest_cache" / "installed" / "blender_mcp" / "core" / "copy.py"
    Source.parent.mkdir(parents=True)
    InstalledCopy.parent.mkdir(parents=True)
    Source.write_text("VALUE = 1\n", encoding="utf-8")
    InstalledCopy.write_text("VALUE = 2\n", encoding="utf-8")

    assert GetSourceFiles(tmp_path) == [Source]
