"""Security regressions for quarantined external provider handlers."""

from __future__ import annotations

import builtins
import importlib
import socket
import tempfile
from dataclasses import dataclass
from enum import Enum
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from blender_mcp.core.enums import Hyper3DAction, PolyHavenAction, SketchfabAction
from blender_mcp.core.security import Capability, ConfigureSecurityPolicy
from blender_mcp.dispatcher import HANDLER_METADATA, HANDLER_REGISTRY, dispatch_command, load_handlers
from blender_mcp.handlers import hyper3d_handler as Hyper3DModule
from blender_mcp.handlers import polyhaven_handler as PolyHavenModule
from blender_mcp.handlers import sketchfab_handler as SketchfabModule


load_handlers()


@dataclass(frozen=True)
class ProviderCase:
    Tool: str
    Module: ModuleType
    ActionEnum: type[Enum]
    StatusAction: str
    ExternalParams: dict[str, dict[str, object]]
    UnsafeNames: tuple[str, ...]


ProviderCases = (
    ProviderCase(
        Tool="integration_polyhaven",
        Module=PolyHavenModule,
        ActionEnum=PolyHavenAction,
        StatusAction=PolyHavenAction.STATUS.value,
        ExternalParams={
            PolyHavenAction.SEARCH.value: {"query": "canary"},
            PolyHavenAction.IMPORT_HDRI.value: {"asset_id": "canary"},
            PolyHavenAction.IMPORT_MODEL.value: {"asset_id": "canary"},
            PolyHavenAction.IMPORT_MATERIAL.value: {"asset_id": "canary"},
        },
        UnsafeNames=(
            "os",
            "tempfile",
            "requests",
            "ContextManagerV3",
            "safe_ops",
            "_search",
            "_import_hdri",
            "_import_model",
            "_import_material",
        ),
    ),
    ProviderCase(
        Tool="integration_sketchfab",
        Module=SketchfabModule,
        ActionEnum=SketchfabAction,
        StatusAction=SketchfabAction.STATUS.value,
        ExternalParams={
            SketchfabAction.SEARCH.value: {"query": "canary"},
            SketchfabAction.GET_DOWNLOAD_URL.value: {"uid": "canary"},
            SketchfabAction.IMPORT.value: {"uid": "canary"},
        },
        UnsafeNames=(
            "os",
            "tempfile",
            "requests",
            "ContextManagerV3",
            "safe_ops",
            "_search",
            "_get_download_url",
            "_import_model",
        ),
    ),
    ProviderCase(
        Tool="integration_hyper3d",
        Module=Hyper3DModule,
        ActionEnum=Hyper3DAction,
        StatusAction=Hyper3DAction.STATUS.value,
        ExternalParams={
            Hyper3DAction.GENERATE.value: {
                "prompt": "canary",
                "image_path": "C:/private/canary.png",
            },
            Hyper3DAction.CHECK_JOB.value: {"job_id": "canary"},
            Hyper3DAction.IMPORT.value: {"model_url": "http://127.0.0.1/model.glb"},
        },
        UnsafeNames=(
            "requests",
            "_create_job",
            "_poll_job",
            "_import_model",
            "_create_job_rodin",
            "_poll_rodin",
            "_create_job_tripo",
            "_poll_tripo",
            "_create_job_meshy",
            "_poll_meshy",
        ),
    ),
)


def Handler(Case: ProviderCase):  # type: ignore[no-untyped-def]
    return getattr(Case.Module, Case.Tool)


@pytest.mark.parametrize("Case", ProviderCases)
def test_provider_actions_have_explicit_capabilities(Case: ProviderCase) -> None:
    Capabilities = HANDLER_METADATA[Case.Tool]["capabilities"]

    assert set(Capabilities) == {Action.value for Action in Case.ActionEnum}
    assert Capabilities[Case.StatusAction] == [Capability.READ.value]
    for Action in Case.ExternalParams:
        assert Capability.NETWORK.value in Capabilities[Action]
        assert Capabilities[Action] != [Capability.READ.value]


@pytest.mark.parametrize("Case", ProviderCases)
def test_dispatcher_denies_external_provider_actions_before_invocation(
    Case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    DeniedHandler = MagicMock(side_effect=AssertionError("quarantined handler was invoked"))
    monkeypatch.setitem(HANDLER_REGISTRY, Case.Tool, DeniedHandler)
    ConfigureSecurityPolicy(SafeMode=False, RawCodeEnabled=True)

    for Action, Params in Case.ExternalParams.items():
        Result = dispatch_command(
            {"tool": Case.Tool, "params": {"action": Action, **Params}},
            use_thread_safety=False,
        )
        assert Result["code"] == "CAPABILITY_DENIED"
        assert Result["is_security_violation"] is True

    DeniedHandler.assert_not_called()


@pytest.mark.parametrize("Case", ProviderCases)
def test_direct_provider_actions_fail_without_file_network_temp_or_blender_io(
    Case: ProviderCase,
) -> None:
    with (
        patch.object(builtins, "open", side_effect=AssertionError("file I/O attempted")) as OpenFile,
        patch.object(
            socket, "create_connection", side_effect=AssertionError("network I/O attempted")
        ) as CreateConnection,
        patch.object(
            tempfile, "mkdtemp", side_effect=AssertionError("temporary directory attempted")
        ) as MakeTempDirectory,
        patch.object(Case.Module.bpy, "ops", MagicMock()) as BlenderOps,
    ):
        for Action, Params in Case.ExternalParams.items():
            Result = Handler(Case)(action=Action, **Params)
            assert Result == {
                "error": Case.Module.ExternalCapabilityMessage,
                "code": "EXTERNAL_CAPABILITY_DISABLED",
                "action": Action,
                "retry_safe": False,
            }

    OpenFile.assert_not_called()
    CreateConnection.assert_not_called()
    MakeTempDirectory.assert_not_called()
    assert BlenderOps.mock_calls == []


@pytest.mark.parametrize("Case", ProviderCases)
def test_retired_provider_sinks_are_removed_and_purged_on_reload(Case: ProviderCase) -> None:
    for Name in Case.UnsafeNames:
        assert not hasattr(Case.Module, Name)
        setattr(Case.Module, Name, MagicMock(name=f"Legacy{Name}"))

    importlib.reload(Case.Module)

    for Name in Case.UnsafeNames:
        assert not hasattr(Case.Module, Name)


@pytest.mark.parametrize(
    "Case,EnableName,ModeName",
    [
        (PolyHavenModule, "blendermcp_use_polyhaven", None),
        (SketchfabModule, "blendermcp_use_sketchfab", None),
        (Hyper3DModule, "blendermcp_use_hyper3d", "blendermcp_hyper3d_mode"),
    ],
)
def test_status_is_truthful_and_does_not_read_scene_credentials(
    Case: ModuleType,
    EnableName: str,
    ModeName: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Scene:
        def __getattr__(self, Name: str) -> object:
            if "key" in Name.lower() or "token" in Name.lower():
                raise AssertionError("status read a Scene credential")
            if Name == EnableName:
                return True
            if ModeName and Name == ModeName:
                return "RODIN"
            raise AttributeError(Name)

    monkeypatch.setattr(Case.bpy.context, "scene", Scene())
    Result = Case._get_status()

    assert Result["success"] is True
    assert Result["configured_enabled"] is True
    assert Result["operational"] is False
    assert Result["external_actions_available"] is False
    if "authenticated" in Result:
        assert Result["authenticated"] is False
