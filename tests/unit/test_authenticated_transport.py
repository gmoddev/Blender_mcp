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
from blender_mcp.core.protocol import recv_message, send_message  # noqa: E402
from blender_mcp.core.session import BuildEnvelope, MessageType  # noqa: E402
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
        assert Response["result"]["observed_request_id"] == "json-rpc-73"
    finally:
        Bridge.CloseConnection()
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
