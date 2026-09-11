"""Security regressions for caller-selected texture-bake output paths."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from blender_mcp.core.enums import BakeAction
from blender_mcp.core.security import Capability
from blender_mcp.dispatcher import HANDLER_METADATA, load_handlers
from blender_mcp.handlers import manage_bake as BakeModule


load_handlers()

BakeHandlers: list[tuple[BakeAction, Callable[..., dict]]] = [
    (BakeAction.BAKE, BakeModule._handle_bake),
    (BakeAction.BAKE_NORMAL, BakeModule._handle_bake_normal),
    (BakeAction.BAKE_AO, BakeModule._handle_bake_ao),
    (BakeAction.BAKE_LIGHTMAP, BakeModule._handle_bake_lightmap),
    (BakeAction.BAKE_COMBINED, BakeModule._handle_bake_combined),
    (BakeAction.BAKE_DIFFUSE, BakeModule._handle_bake_diffuse),
    (BakeAction.BAKE_GLOSSY, BakeModule._handle_bake_glossy),
    (BakeAction.BAKE_SHADOW, BakeModule._handle_bake_shadow),
    (BakeAction.BAKE_DISPLACEMENT, BakeModule._handle_bake_displacement),
    (BakeAction.BAKE_EMISSION, BakeModule._handle_bake_emission),
]


def ErrorCode(Result: dict) -> str:
    return str(Result["errors"][0]["code"])


@pytest.mark.parametrize("Action", sorted(BakeModule.BakeOutputActions))
def test_bake_actions_declare_filesystem_write_authority(Action: str) -> None:
    Required = HANDLER_METADATA["manage_bake"]["capabilities"][Action]

    assert Required == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]


@pytest.mark.parametrize("Action", sorted(BakeModule.BakeOutputActions))
def test_registered_bake_output_path_fails_before_blender_access(
    Action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Scene = MagicMock(side_effect=AssertionError("scene should not be inspected"))
    Execute = MagicMock(side_effect=AssertionError("bake should not execute"))
    monkeypatch.setattr(BakeModule.ContextManagerV3, "get_scene", Scene)
    monkeypatch.setattr(BakeModule, "execute_on_main_thread", Execute)

    Result = BakeModule.manage_bake(action=Action, output_path="C:/outside/texture.png")

    assert Result["success"] is False
    assert ErrorCode(Result) == "BAKE_OUTPUT_PATH_DISABLED"
    Scene.assert_not_called()
    Execute.assert_not_called()


@pytest.mark.parametrize("Action,Call", BakeHandlers)
def test_direct_bake_output_path_fails_before_blender_access(
    Action: BakeAction,
    Call: Callable[..., dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Scene = MagicMock(side_effect=AssertionError("scene should not be inspected"))
    Execute = MagicMock(side_effect=AssertionError("bake should not execute"))
    monkeypatch.setattr(BakeModule.ContextManagerV3, "get_scene", Scene)
    monkeypatch.setattr(BakeModule, "execute_on_main_thread", Execute)

    Result = Call(output_path="C:/outside/texture.png")

    assert Result["success"] is False
    assert ErrorCode(Result) == "BAKE_OUTPUT_PATH_DISABLED"
    assert Result["metadata"]["action"] == Action.value
    Scene.assert_not_called()
    Execute.assert_not_called()


def test_low_level_bake_wrapper_rejects_external_output_before_context_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Execute = MagicMock(side_effect=AssertionError("bake should not execute"))
    monkeypatch.setattr(BakeModule, "execute_on_main_thread", Execute)

    Success, Error = BakeModule._execute_bake_with_context(
        object(),
        "NORMAL",
        filepath="C:/outside/texture.png",
        save_mode="EXTERNAL",
    )

    assert Success is False
    assert Error == "External bake output paths are disabled pending filesystem authorization"
    Execute.assert_not_called()
