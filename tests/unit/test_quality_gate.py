"""Tests for the strict quality gate's control-plane typing scope."""

import ast
from pathlib import Path

from scripts.quality.run_checks import BuildMypyControlPlaneCommand, MypyControlPlaneTargets


ExpectedMypyTargets = {
    "blender_mcp/core/protocol.py",
    "blender_mcp/core/session.py",
    "blender_mcp/core/security.py",
    "blender_mcp/core/filesystem_boundary.py",
    "blender_mcp/core/logging_config.py",
    "stdio_bridge.py",
}
AllowedDirectBpyTargets = {"blender_mcp/core/logging_config.py"}


def test_mypy_scope_covers_the_blender_independent_control_plane() -> None:
    assert set(MypyControlPlaneTargets) == ExpectedMypyTargets


def test_mypy_scope_limits_direct_blender_imports_to_logging_metadata() -> None:
    for Target in MypyControlPlaneTargets:
        Tree = ast.parse(Path(Target).read_text(encoding="utf-8"))
        for Node in ast.walk(Tree):
            if isinstance(Node, ast.Import):
                ImportsBpy = any(Alias.name == "bpy" for Alias in Node.names)
                if ImportsBpy:
                    assert Target in AllowedDirectBpyTargets
            elif isinstance(Node, ast.ImportFrom):
                if Node.module == "bpy":
                    assert Target in AllowedDirectBpyTargets


def test_mypy_command_does_not_follow_blender_bound_imports() -> None:
    Command = BuildMypyControlPlaneCommand()

    assert "--follow-imports=skip" in Command
    assert tuple(Command[-len(MypyControlPlaneTargets) :]) == MypyControlPlaneTargets
