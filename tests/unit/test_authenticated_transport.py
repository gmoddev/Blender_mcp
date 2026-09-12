"""Loopback server integration tests without a Blender runtime."""

from __future__ import annotations

import base64
import os
import socket
import sys
import time
from unittest.mock import MagicMock, patch

# The transport boundary is intentionally tested without importing the installed Blender stubs.
sys.modules.setdefault("bpy", MagicMock())
sys.modules.setdefault("mathutils", MagicMock())
sys.modules.setdefault("mathutils.bvhtree", MagicMock())
sys.modules.setdefault("bmesh", MagicMock())

from blender_mcp import BlenderMCPServer  # noqa: E402
from blender_mcp.core.filesystem_boundary import ResetFilesystemPolicy  # noqa: E402
from blender_mcp.core.security import GetSecurityPolicy, ResetSecurityPolicy  # noqa: E402
from blender_mcp.core.protocol import recv_message, send_message  # noqa: E402
from blender_mcp.core.session import (  # noqa: E402
    BuildEnvelope,
    BuildScopedRequestId,
    MessageType,
    NormalizeJsonRpcRequestId,
)
from stdio_bridge import MCPBridge  # noqa: E402

TOKEN = base64.urlsafe_b64encode(bytes(range(64, 96))).decode("ascii").rstrip("=")
WRONG_TOKEN = base64.urlsafe_b64encode(bytes(range(96, 128))).decode("ascii").rstrip("=")
ROTATED_TOKEN = base64.urlsafe_b64encode(bytes(range(128, 160))).decode("ascii").rstrip("=")


def StartServer(MaxActiveClients: int = 4) -> BlenderMCPServer:
    Server = BlenderMCPServer(
        host="127.0.0.1",
        port=0,
        auth_token=TOKEN,
        max_active_clients=MaxActiveClients,
        handshake_timeout=1.0,
        body_timeout=1.0,
        idle_timeout=2.0,
    )
    assert Server.start()
    return Server


def test_unauthenticated_request_cannot_dispatch() -> None:
    Server = StartServer()
    Executed = []
    Server.execute_command = lambda Command: Executed.append(Command)  # type: ignore[method-assign]
    Client = socket.create_connection(("127.0.0.1", Server.port), timeout=2)
    try:
        Challenge = recv_message(Client)
        assert Challenge is not None
        send_message(
            Client,
            BuildEnvelope(
                MessageType.REQUEST,
                "request-before-auth",
                Challenge["session_id"],
                {"tool": "list_all_tools", "params": {"action": "list_all_tools"}},
            ),
        )
        Error = recv_message(Client)
        assert Error is not None
        assert Error["message_type"] == MessageType.ERROR.value
        assert Error["payload"]["error"]["code"] == "AUTH_REQUIRED"
        assert Executed == []
    finally:
        Client.close()
        Server.stop()


def test_authenticated_bridge_preserves_request_id() -> None:
    Server = StartServer()

    def Execute(Command: dict) -> dict:
        return {
            "status": "success",
            "result": {"observed_request_id": Command["request_id"]},
        }

    Server.execute_command = Execute  # type: ignore[method-assign]
    Bridge = MCPBridge(host="127.0.0.1", port=Server.port, auth_token=TOKEN)
    try:
        Response = Bridge.send_to_blender(
            {
                "tool": "get_server_status",
                "params": {"action": "get_server_status"},
                "request_id": "json-rpc-73",
            },
            retries=0,
        )
        assert Response["status"] == "success"
        assert Response["result"]["observed_request_id"] == BuildScopedRequestId(
            Bridge.ClientInstanceId, "json-rpc-73"
        )
    finally:
        Bridge.CloseConnection()
        Server.stop()


def test_json_rpc_request_ids_preserve_value_type() -> None:
    assert NormalizeJsonRpcRequestId(1) != NormalizeJsonRpcRequestId("1")


def test_bridge_namespace_is_stable_across_reconnect_and_isolated_between_bridges() -> None:
    Server = StartServer()
    Observed = []

    def Execute(Command: dict) -> dict:
        Observed.append(Command["request_id"])
        return {"status": "success", "result": {"ok": True}}

    Server.execute_command = Execute  # type: ignore[method-assign]
    BridgeA = MCPBridge(host="127.0.0.1", port=Server.port, auth_token=TOKEN)
    BridgeB = MCPBridge(host="127.0.0.1", port=Server.port, auth_token=TOKEN)
    try:
        Command = {
            "tool": "get_server_status",
            "params": {"action": "get_server_status"},
            "request_id": "same-wire-id",
        }
        assert BridgeA.send_to_blender(Command, retries=0)["status"] == "success"
        BridgeA.CloseConnection()
        assert BridgeA.send_to_blender(Command, retries=0)["status"] == "success"
        assert BridgeB.send_to_blender(Command, retries=0)["status"] == "success"
        assert Observed[0] == Observed[1]
        assert Observed[0] != Observed[2]
    finally:
        BridgeA.CloseConnection()
        BridgeB.CloseConnection()
        Server.stop()


def test_lifecycle_target_is_scoped_to_authenticated_bridge_and_not_exposed() -> None:
    Server = StartServer()
    ObservedTargets = []

    def Execute(Command: dict) -> dict:
        Target = Command["params"]["target_request_id"]
        ObservedTargets.append(Target)
        return {
            "status": "success",
            "result": {"request_id": Target, "target_request_id": Target},
            "_meta": {"request_id": Command["request_id"]},
        }

    Server.execute_command = Execute  # type: ignore[method-assign]
    BridgeA = MCPBridge(host="127.0.0.1", port=Server.port, auth_token=TOKEN)
    BridgeB = MCPBridge(host="127.0.0.1", port=Server.port, auth_token=TOKEN)
    Command = {
        "tool": "manage_command_lifecycle",
        "params": {"action": "GET_STATUS", "target_request_id": "original-request"},
        "request_id": "status-request",
    }
    try:
        ResponseA = BridgeA.send_to_blender(Command, retries=0)
        ResponseB = BridgeB.send_to_blender(Command, retries=0)
        assert ObservedTargets[0] != ObservedTargets[1]
        assert ResponseA["result"]["request_id"] == "original-request"
        assert ResponseA["result"]["target_request_id"] == "original-request"
        assert ResponseA["_meta"]["request_id"] == "status-request"
        assert ResponseB["result"]["request_id"] == "original-request"
    finally:
        BridgeA.CloseConnection()
        BridgeB.CloseConnection()
        Server.stop()


def test_wrong_credential_cannot_connect() -> None:
    Server = StartServer()
    Bridge = MCPBridge(
        host="127.0.0.1",
        port=Server.port,
        auth_token=WRONG_TOKEN,
    )
    try:
        assert not Bridge.connect()
        assert Bridge._LastErrorCode == "AUTH_FAILED"
    finally:
        Bridge.CloseConnection()
        Server.stop()


def test_bridge_rejects_remote_host_before_socket_creation() -> None:
    Bridge = MCPBridge(host="192.0.2.10", port=9879, auth_token=TOKEN)
    assert not Bridge.connect()
    assert Bridge._LastErrorCode == "REMOTE_HOST_DISABLED"


def test_preference_rotation_wins_over_stale_environment_after_restart() -> None:
    Addon = MagicMock()
    Addon.preferences.auth_token = ROTATED_TOKEN
    with (
        patch.dict(os.environ, {"BLENDER_MCP_AUTH_TOKEN": TOKEN}),
        patch("blender_mcp.bpy.context.preferences.addons.get", return_value=Addon),
    ):
        RestartedServer = BlenderMCPServer()
        assert RestartedServer.GetAuthToken() == ROTATED_TOKEN


def test_filesystem_preferences_are_user_scoped_and_typed() -> None:
    Addon = MagicMock()
    Addon.preferences.filesystem_read_root = " C:/approved/read "
    Addon.preferences.filesystem_write_root = " C:/approved/write "
    Addon.preferences.allow_filesystem_overwrite = True

    with patch("blender_mcp.bpy.context.preferences.addons.get", return_value=Addon):
        assert BlenderMCPServer.GetFilesystemPreferences() == (
            "C:/approved/read",
            "C:/approved/write",
            True,
        )


def test_security_preferences_are_snapshotted_and_typed() -> None:
    Addon = MagicMock()
    Addon.preferences.safe_mode = False
    Addon.preferences.raw_code_enabled = True

    with patch("blender_mcp.bpy.context.preferences.addons.get", return_value=Addon):
        assert BlenderMCPServer.GetSecurityPreferences() == (False, True)

    Addon.preferences.safe_mode = "false"
    Addon.preferences.raw_code_enabled = 1
    with patch("blender_mcp.bpy.context.preferences.addons.get", return_value=Addon):
        assert BlenderMCPServer.GetSecurityPreferences() == (True, False)


def test_listener_start_installs_effective_security_snapshot() -> None:
    ResetSecurityPolicy()
    Server = BlenderMCPServer(host="127.0.0.1", port=0, auth_token=TOKEN)
    try:
        with patch.object(Server, "GetSecurityPreferences", return_value=(False, True)):
            assert Server.start()
            Policy = GetSecurityPolicy()
            assert Policy.SafeMode is False
            assert Policy.RawCodeEnabled is True
    finally:
        Server.stop()
        ResetSecurityPolicy()


def test_worker_thread_start_fails_before_reading_blender_preferences() -> None:
    Server = BlenderMCPServer(host="127.0.0.1", port=0, auth_token=TOKEN)
    AuthRead = MagicMock(side_effect=AssertionError("worker startup read auth preferences"))
    FilesystemRead = MagicMock(
        side_effect=AssertionError("worker startup read filesystem preferences")
    )
    SecurityRead = MagicMock(side_effect=AssertionError("worker startup read security preferences"))
    Server.GetAuthToken = AuthRead  # type: ignore[method-assign]
    Server.GetFilesystemPreferences = FilesystemRead  # type: ignore[method-assign]
    Server.GetSecurityPreferences = SecurityRead  # type: ignore[method-assign]

    with (
        patch("blender_mcp.bpy.is_mock", False),
        patch("blender_mcp.core.thread_safety.is_main_thread", return_value=False),
    ):
        assert Server.start() is False

    AuthRead.assert_not_called()
    FilesystemRead.assert_not_called()
    SecurityRead.assert_not_called()
    assert Server.running is False
    assert Server.socket is None


def test_invalid_filesystem_root_prevents_listener_start() -> None:
    Server = BlenderMCPServer(host="127.0.0.1", port=0, auth_token=TOKEN)
    try:
        with patch.object(
            Server,
            "GetFilesystemPreferences",
            return_value=("Z:/definitely/missing/root", "", False),
        ):
            assert Server.start() is False
        assert Server.running is False
        assert Server.socket is None
    finally:
        Server.stop()
        ResetFilesystemPolicy()


def test_socket_write_failure_is_indeterminate() -> None:
    Bridge = MCPBridge(host="127.0.0.1", port=9879, auth_token=TOKEN)
    Bridge.client_socket = MagicMock(spec=socket.socket)
    Bridge.Session = MagicMock()
    Bridge.Session.BuildRequest.return_value = {"protocol_version": 1}
    with patch("blender_mcp.core.protocol.send_message", side_effect=OSError("reset")):
        Response = Bridge.send_to_blender(
            {
                "tool": "manage_scene",
                "params": {"action": "RENAME"},
                "request_id": "write-failure",
            },
            retries=0,
        )
    assert Response["code"] == "REQUEST_INDETERMINATE"


def test_active_client_limit_rejects_excess_connection() -> None:
    Server = StartServer(MaxActiveClients=1)
    First = socket.create_connection(("127.0.0.1", Server.port), timeout=2)
    try:
        assert recv_message(First) is not None
        Second = socket.create_connection(("127.0.0.1", Server.port), timeout=2)
        try:
            Rejection = recv_message(Second)
            assert Rejection is not None
            assert Rejection["payload"]["error"]["code"] == "RESOURCE_LIMIT"
        finally:
            Second.close()
    finally:
        First.close()
        Server.stop()


def test_rotation_revokes_authenticated_socket() -> None:
    Server = StartServer()
    Bridge = MCPBridge(host="127.0.0.1", port=Server.port, auth_token=TOKEN)
    try:
        assert Bridge.connect()
        PreviousInstance = Bridge.Session.InstanceId
        Server.RotateAuthToken(ROTATED_TOKEN)
        time.sleep(0.05)
        assert Server.InstanceId != PreviousInstance
        Response = Bridge.send_to_blender(
            {
                "tool": "get_server_status",
                "params": {"action": "get_server_status"},
                "request_id": "after-rotation",
            },
            retries=0,
        )
        assert Response["code"] == "REQUEST_INDETERMINATE"
    finally:
        Bridge.CloseConnection()
        Server.stop()


def test_server_preserves_structured_lifecycle_error_fields() -> None:
    Server = BlenderMCPServer(auth_token=TOKEN)
    DispatcherResult = {
        "error": "Command was running at timeout",
        "code": "REQUEST_INDETERMINATE",
        "command_state": "running_after_timeout",
        "retry_safe": False,
        "terminal": False,
        "_meta": {
            "request_id": "lifecycle-error",
            "command_state": "running_after_timeout",
        },
    }
    with patch("blender_mcp.dispatcher.dispatch_command", return_value=DispatcherResult):
        Result = Server._execute_command_internal(
            {
                "tool": "list_all_tools",
                "params": {"action": "list_all_tools"},
                "request_id": "lifecycle-error",
            }
        )

    assert Result["status"] == "error"
    assert Result["error"]["code"] == "REQUEST_INDETERMINATE"
    assert Result["error"]["command_state"] == "running_after_timeout"
    assert Result["error"]["retry_safe"] is False
    assert Result["_meta"]["request_id"] == "lifecycle-error"
