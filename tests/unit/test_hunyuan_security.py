"""Security regression tests for the quarantined Hunyuan boundary."""

from __future__ import annotations

import builtins
import importlib
import os
import socket
import sys
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

BpyMock = MagicMock()
BpyMock.app.version = (5, 0, 0)
sys.modules.setdefault("bpy", BpyMock)
sys.modules.setdefault("mathutils", MagicMock())

from blender_mcp.core.enums import HunyuanAction  # noqa: E402
from blender_mcp.core.security import Capability, ConfigureSecurityPolicy  # noqa: E402
from blender_mcp.dispatcher import (  # noqa: E402
    HANDLER_METADATA,
    HANDLER_REGISTRY,
    dispatch_command,
    load_handlers,
)

load_handlers()

from blender_mcp.handlers import hunyuan_handler as HunyuanModule  # noqa: E402


ExternalActionCases = [
    (HunyuanAction.GENERATE.value, {"image_path": "C:/private/canary.txt"}),
    (HunyuanAction.GENERATE.value, {"image_path": "file:///C:/private/canary.txt"}),
    (HunyuanAction.GENERATE.value, {"image_path": "HTTP://127.0.0.1/private"}),
    (HunyuanAction.CHECK_JOB.value, {"job_id": "job_canary"}),
    (HunyuanAction.IMPORT.value, {"zip_url": "http://127.0.0.1:8080/private"}),
    (HunyuanAction.IMPORT.value, {"zip_url": "http://[::1]/private"}),
    (HunyuanAction.IMPORT.value, {"zip_url": "http://public.example@127.0.0.1/private"}),
    (HunyuanAction.IMPORT.value, {"zip_url": "//127.0.0.1/private"}),
]


def test_hunyuan_actions_have_explicit_least_privilege_capabilities() -> None:
    Capabilities = HANDLER_METADATA["integration_hunyuan"]["capabilities"]

    assert Capabilities == {
        HunyuanAction.STATUS.value: [Capability.READ.value],
        HunyuanAction.GENERATE.value: [
            Capability.MUTATE.value,
            Capability.FILESYSTEM_READ.value,
            Capability.FILESYSTEM_WRITE.value,
            Capability.NETWORK.value,
            Capability.CREDENTIAL_ACCESS.value,
        ],
        HunyuanAction.CHECK_JOB.value: [
            Capability.READ.value,
            Capability.NETWORK.value,
            Capability.CREDENTIAL_ACCESS.value,
        ],
        HunyuanAction.IMPORT.value: [
            Capability.MUTATE.value,
            Capability.FILESYSTEM_READ.value,
            Capability.NETWORK.value,
            Capability.FILESYSTEM_WRITE.value,
        ],
    }


@pytest.mark.parametrize("Action, Params", ExternalActionCases)
def test_dispatcher_denies_external_actions_before_handler_invocation(
    monkeypatch: pytest.MonkeyPatch,
    Action: str,
    Params: dict[str, str],
) -> None:
    Handler = MagicMock(side_effect=AssertionError("quarantined handler was invoked"))
    monkeypatch.setitem(HANDLER_REGISTRY, "integration_hunyuan", Handler)
    ConfigureSecurityPolicy(SafeMode=False, RawCodeEnabled=True)

    Result = dispatch_command(
        {
            "tool": "integration_hunyuan",
            "params": {"action": Action, **Params},
        },
        use_thread_safety=False,
    )

    assert Result["code"] == "CAPABILITY_DENIED"
    assert Result["is_security_violation"] is True
    Handler.assert_not_called()


@pytest.mark.parametrize("Action, Params", ExternalActionCases)
def test_direct_handler_calls_fail_closed_without_file_network_or_timer_io(
    Action: str,
    Params: dict[str, str],
) -> None:
    with (
        patch.object(
            builtins, "open", side_effect=AssertionError("file I/O attempted")
        ) as OpenFile,
        patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("network I/O attempted"),
        ) as CreateConnection,
        patch.object(
            tempfile,
            "mkdtemp",
            side_effect=AssertionError("temporary directory creation attempted"),
        ) as MakeTempDirectory,
        patch.object(
            tempfile,
            "NamedTemporaryFile",
            side_effect=AssertionError("temporary file creation attempted"),
        ) as MakeTempFile,
        patch.object(
            zipfile,
            "ZipFile",
            side_effect=AssertionError("archive access attempted"),
        ) as OpenArchive,
        patch.object(
            HunyuanModule.bpy.app.timers,
            "register",
            side_effect=AssertionError("timer I/O attempted"),
        ) as RegisterTimer,
        patch.object(HunyuanModule.bpy, "ops", MagicMock()) as BlenderOps,
    ):
        Result = HunyuanModule.integration_hunyuan(action=Action, **Params)

    assert Result == {
        "error": HunyuanModule.ExternalCapabilityMessage,
        "code": "EXTERNAL_CAPABILITY_DISABLED",
        "action": Action,
        "retry_safe": False,
    }
    OpenFile.assert_not_called()
    CreateConnection.assert_not_called()
    MakeTempDirectory.assert_not_called()
    MakeTempFile.assert_not_called()
    OpenArchive.assert_not_called()
    RegisterTimer.assert_not_called()
    assert BlenderOps.mock_calls == []


def test_unsafe_hunyuan_io_helpers_are_removed() -> None:
    for Name in (
        "base64",
        "datetime",
        "hashlib",
        "hmac",
        "json",
        "os",
        "osp",
        "tempfile",
        "time",
        "zipfile",
        "requests",
        "ContextManagerV3",
        "safe_ops",
        "_create_job",
        "_create_job_official",
        "_create_job_local",
        "_poll_job",
        "_import_asset",
        "get_tencent_cloud_sign_headers",
    ):
        assert not hasattr(HunyuanModule, Name)


def test_hot_reload_purges_legacy_hunyuan_sinks() -> None:
    LegacyNames = (
        "requests",
        "safe_ops",
        "_create_job",
        "_poll_job",
        "_import_asset",
        "_create_job_official",
        "_create_job_local",
        "get_tencent_cloud_sign_headers",
    )
    for Name in LegacyNames:
        setattr(HunyuanModule, Name, MagicMock(name=f"Legacy{Name}"))

    importlib.reload(HunyuanModule)

    for Name in LegacyNames:
        assert not hasattr(HunyuanModule, Name)

    Result = HunyuanModule.integration_hunyuan(
        action=HunyuanAction.IMPORT.value,
        zip_url="http://127.0.0.1/private",
    )
    assert Result["code"] == "EXTERNAL_CAPABILITY_DISABLED"


def test_status_remains_available_in_safe_mode_and_is_truthful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureSecurityPolicy(SafeMode=True)
    monkeypatch.setattr(
        HunyuanModule.bpy.context.scene,
        "blendermcp_use_hunyuan3d",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        HunyuanModule.bpy.context.scene,
        "blendermcp_hunyuan3d_mode",
        "LOCAL_API",
        raising=False,
    )

    Result = dispatch_command(
        {
            "tool": "integration_hunyuan",
            "params": {"action": HunyuanAction.STATUS.value},
        },
        use_thread_safety=False,
    )

    assert Result["success"] is True
    assert Result["enabled"] is True
    assert Result["configured_enabled"] is True
    assert Result["operational"] is False
    assert Result["external_actions_available"] is False
    assert Result["mode"] == "LOCAL_API"
    assert "disabled by security policy" in Result["message"]
