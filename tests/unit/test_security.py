"""Unit tests for fail-closed capability authorization."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

bpy_mock = MagicMock()
bpy_mock.app.version = (5, 0, 0)
sys.modules.setdefault("bpy", bpy_mock)
sys.modules.setdefault("mathutils", MagicMock())

from blender_mcp.core.security import Capability, SecurityManager  # noqa: E402
from blender_mcp.dispatcher import dispatch_command, load_handlers  # noqa: E402

load_handlers()


class TestSecurityManager:
    def test_safe_mode_allows_explicit_read(self) -> None:
        with patch.object(SecurityManager, "is_safe_mode", return_value=True):
            assert SecurityManager.validate_action("inspect", "GET", [Capability.READ.value])

    def test_safe_mode_denies_structured_mutation(self) -> None:
        with patch.object(SecurityManager, "is_safe_mode", return_value=True):
            assert not SecurityManager.validate_action(
                "manage_scene", "DELETE_ALL", [Capability.MUTATE.value]
            )

    def test_safe_mode_denies_raw_python(self) -> None:
        with patch.object(SecurityManager, "is_safe_mode", return_value=True):
            assert not SecurityManager.validate_action(
                "execute_blender_code", "execute_blender_code", [Capability.EXECUTE_CODE.value]
            )

    def test_full_structured_mode_still_denies_raw_python(self) -> None:
        with (
            patch.object(SecurityManager, "is_safe_mode", return_value=False),
            patch.object(SecurityManager, "is_raw_code_enabled", return_value=False),
        ):
            assert SecurityManager.validate_action(
                "manage_scene", "RENAME", [Capability.MUTATE.value]
            )
            assert not SecurityManager.validate_action(
                "execute_blender_code", "execute_blender_code", [Capability.EXECUTE_CODE.value]
            )

    def test_raw_python_requires_separate_enablement(self) -> None:
        with (
            patch.object(SecurityManager, "is_safe_mode", return_value=False),
            patch.object(SecurityManager, "is_raw_code_enabled", return_value=True),
        ):
            assert SecurityManager.validate_action(
                "execute_blender_code", "execute_blender_code", [Capability.EXECUTE_CODE.value]
            )

    def test_unknown_and_unclassified_actions_deny(self) -> None:
        with patch.object(SecurityManager, "is_safe_mode", return_value=False):
            assert not SecurityManager.validate_action("nonexistent", "ANY", None)
            assert not SecurityManager.validate_action("known", "ANY", [])
            assert not SecurityManager.validate_action("known", "ANY", ["NOT_A_CAPABILITY"])
            assert not SecurityManager.validate_action("", "", [Capability.READ.value])

    def test_is_safe_mode_returns_bool(self) -> None:
        assert isinstance(SecurityManager.is_safe_mode(), bool)


class TestRawCodeDispatchBoundary:
    def test_all_raw_execution_routes_are_denied_in_safe_mode(self) -> None:
        Commands = [
            {
                "tool": "manage_scripting",
                "params": {"action": "EXECUTE_CODE", "code": "raise AssertionError"},
            },
            {
                "tool": "manage_scripting",
                "params": {"action": "EXECUTE_TEXT_BLOCK", "name": "Canary.py"},
            },
            {
                "tool": "execute_blender_code",
                "params": {"action": "execute_blender_code", "code": "raise AssertionError"},
            },
            {
                "tool": "execute_code",
                "params": {"action": "execute_code", "code": "raise AssertionError"},
            },
        ]
        with patch.object(SecurityManager, "is_safe_mode", return_value=True):
            for Command in Commands:
                Result = dispatch_command(Command, use_thread_safety=False)
                assert Result["code"] == "CAPABILITY_DENIED"

    def test_raw_execution_stays_denied_without_explicit_raw_mode(self) -> None:
        Command = {
            "tool": "execute_blender_code",
            "params": {"action": "execute_blender_code", "code": "raise AssertionError"},
        }
        with (
            patch.object(SecurityManager, "is_safe_mode", return_value=False),
            patch.object(SecurityManager, "is_raw_code_enabled", return_value=False),
        ):
            Result = dispatch_command(Command, use_thread_safety=False)
        assert Result["code"] == "CAPABILITY_DENIED"
