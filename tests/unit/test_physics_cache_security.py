"""Security regressions for scene-derived physics cache paths."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from blender_mcp.core.enums import PhysicsAction
from blender_mcp.core.security import Capability
from blender_mcp.dispatcher import HANDLER_METADATA, load_handlers
from blender_mcp.handlers import manage_physics as PhysicsModule


load_handlers()

CacheActions = sorted(PhysicsModule.PhysicsCacheActions)


def ErrorCode(Result: dict) -> str:
    return str(Result["errors"][0]["code"])


@pytest.mark.parametrize("Action", CacheActions)
def test_physics_cache_routes_require_write_authority_and_fail_closed(Action: str) -> None:
    Required = HANDLER_METADATA["manage_physics"]["capabilities"][Action]

    assert Required == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]
    Result = PhysicsModule.manage_physics(action=Action)
    assert Result["success"] is False
    assert ErrorCode(Result) == "CACHE_FAMILY_DISABLED"


@pytest.mark.parametrize(
    "Action,Call",
    [
        (PhysicsAction.RIGID_BODY_BAKE.value, PhysicsModule._handle_rigid_body_bake),
        (PhysicsAction.CLOTH_BAKE.value, PhysicsModule._handle_cloth_bake),
        (PhysicsAction.FLUID_BAKE.value, PhysicsModule._handle_fluid_bake),
        (PhysicsAction.PARTICLE_BAKE.value, PhysicsModule._handle_particle_bake),
        (PhysicsAction.SOFT_BODY_BAKE.value, PhysicsModule._handle_soft_body_bake),
        (PhysicsAction.ALL_BAKE.value, PhysicsModule._handle_all_bake),
        (PhysicsAction.SIMULATION_PLAY.value, PhysicsModule._handle_simulation_play),
        (PhysicsAction.ALL_CACHE_CLEAR.value, PhysicsModule._handle_all_cache_clear),
        (
            PhysicsAction.RIGID_BODY_CACHE_CLEAR.value,
            lambda: PhysicsModule._handle_cache_clear("RIGID_BODY"),
        ),
        (
            PhysicsAction.CLOTH_CACHE_CLEAR.value,
            lambda: PhysicsModule._handle_cache_clear("CLOTH", "Cloth"),
        ),
        (
            PhysicsAction.FLUID_CACHE_CLEAR.value,
            lambda: PhysicsModule._handle_cache_clear("FLUID"),
        ),
    ],
)
def test_direct_physics_cache_helpers_have_no_blender_sink(
    Action: str,
    Call: Callable[[], dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Execute = MagicMock(side_effect=AssertionError("cache operation should not execute"))
    Scene = MagicMock(side_effect=AssertionError("scene should not be inspected"))
    monkeypatch.setattr(PhysicsModule, "execute_on_main_thread", Execute)
    monkeypatch.setattr(PhysicsModule.ContextManagerV3, "get_scene", Scene)

    Result = Call()

    assert Result["success"] is False
    assert ErrorCode(Result) == "CACHE_FAMILY_DISABLED"
    assert Result["metadata"]["action"] == Action
    Execute.assert_not_called()
    Scene.assert_not_called()


@pytest.mark.parametrize(
    "Action,Call",
    [
        (
            PhysicsAction.RIGID_BODY_WORLD_SETUP.value,
            PhysicsModule._handle_rigid_body_world_setup,
        ),
        (PhysicsAction.CLOTH_SIM_SETUP.value, PhysicsModule._handle_cloth_setup),
        (PhysicsAction.FLUID_DOMAIN_SETUP.value, PhysicsModule._handle_fluid_domain_setup),
    ],
)
def test_custom_cache_paths_are_denied_before_scene_mutation(
    Action: str,
    Call: Callable[..., dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Scene = MagicMock(side_effect=AssertionError("scene should not be inspected"))
    monkeypatch.setattr(PhysicsModule.ContextManagerV3, "get_scene", Scene)

    Result = Call(cache_path="C:/outside/cache")

    assert Result["success"] is False
    assert ErrorCode(Result) == "CACHE_PATH_DISABLED"
    assert Result["metadata"]["action"] == Action
    Scene.assert_not_called()
