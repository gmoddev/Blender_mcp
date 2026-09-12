bl_info = {
    "name": "Blender MCP",
    "author": "GÖKSEL ÖZKAN (GitHub: glonorce)",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),  # High Mode: Blender 5.0+ only, no backward compatibility
    "location": "View3D > Sidebar > MCP",
    "description": "Generic local Blender Model Context Protocol server (v1.0.0)",
    "category": "Development",
    "support": "COMMUNITY",
    "doc_url": "https://github.com/gmoddev/Blender_mcp",
    "tracker_url": "https://github.com/gmoddev/Blender_mcp/issues",
}


# Try to import bpy, if it fails (running outside Blender), create a mock.
# This allows the MCP server to start and list tools even in a standard Python env.
import sys
from types import ModuleType
from typing import cast, Optional, Any
from unittest.mock import MagicMock


def _ensure_bpy_contract(bpy_module):
    """Guarantee minimum bpy contract for headless/CI environments."""
    patched = False
    for attr in ("ops", "props", "types", "context", "data", "path"):
        if not hasattr(bpy_module, attr):
            setattr(bpy_module, attr, MagicMock(name=f"bpy.{attr}"))
            patched = True

    if not hasattr(bpy_module, "app"):
        mock_app = MagicMock(name="bpy.app")
        mock_app.background = False
        mock_app.version = (0, 0, 0)
        setattr(bpy_module, "app", mock_app)
        patched = True

    sys.modules.setdefault("bpy.props", getattr(bpy_module, "props"))
    sys.modules.setdefault("bpy.types", getattr(bpy_module, "types"))
    sys.modules.setdefault("bpy.ops", getattr(bpy_module, "ops"))
    sys.modules.setdefault("bpy.context", getattr(bpy_module, "context"))

    path_module = getattr(bpy_module, "path")
    if not hasattr(path_module, "abspath"):
        setattr(path_module, "abspath", lambda p: p)
        patched = True

    if "bmesh" not in sys.modules:
        sys.modules["bmesh"] = MagicMock(name="bmesh")

    if "mathutils" not in sys.modules:
        mock_mathutils = MagicMock(name="mathutils")
        mock_mathutils.Vector = MagicMock
        mock_mathutils.Matrix = MagicMock
        mock_mathutils.Quaternion = MagicMock
        mock_mathutils.Color = MagicMock
        sys.modules["mathutils"] = mock_mathutils

    return patched


try:
    import bpy

    was_patched = _ensure_bpy_contract(bpy)
    if was_patched and not hasattr(bpy, "is_mock"):
        setattr(bpy, "is_mock", True)
except ImportError:
    mock_bpy = ModuleType("bpy")
    setattr(mock_bpy, "ops", MagicMock(name="bpy.ops"))
    setattr(mock_bpy, "props", MagicMock(name="bpy.props"))
    setattr(mock_bpy, "types", MagicMock(name="bpy.types"))
    setattr(mock_bpy, "context", MagicMock(name="bpy.context"))
    setattr(mock_bpy, "data", MagicMock(name="bpy.data"))
    setattr(mock_bpy, "is_mock", True)
    sys.modules["bpy"] = mock_bpy
    _ensure_bpy_contract(mock_bpy)
    import bpy

if not hasattr(bpy, "is_mock"):
    setattr(bpy, "is_mock", False)

import importlib
import ipaddress
import json

# STAFF+ DEBUG LOGGING
import logging
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid

SERVER_LOG_FILE = os.path.join(tempfile.gettempdir(), "blender_server_debug.log")
server_logger = logging.getLogger("blender_mcp_server")
server_logger.setLevel(logging.DEBUG)
# Remove old handlers to prevent dupes on reload
if server_logger.hasHandlers():
    server_logger.handlers.clear()
# Rotating handler: 5 MB limit, 3 backups, UTF-8 for Windows compatibility
from logging.handlers import RotatingFileHandler as _RotatingFileHandler

handler = _RotatingFileHandler(
    SERVER_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
server_logger.addHandler(handler)


def log_debug(msg):
    try:
        server_logger.info(msg)
        # print(f"[MCP DEBUG] {msg}") # DISABLE CONSOLE PRINT to prevent Stdio Corruption
    except:
        pass


log_debug("=== BLENDER SERVER LOADED (V1.0.0 - HIGH MODE UNLEASHED) ===")

# Add the PARENT directory to sys.path to ensure 'blender_mcp' package resolution works
# This is critical for absolute imports like 'from blender_mcp.core import ...'
package_dir = os.path.dirname(os.path.realpath(__file__))  # .../blender_mcp
parent_dir = os.path.dirname(package_dir)  # .../blender-mcp
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# High Mode: Handle missing props gracefully (fake-bpy-module compatibility)
try:
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
except ImportError:
    # Fallback for fake-bpy-module or missing props
    def BoolProperty(**kwargs) -> Any:  # type: ignore[misc]
        return cast(Any, "PROPERTY")

    def EnumProperty(**kwargs) -> Any:  # type: ignore[misc]
        return cast(Any, "PROPERTY")

    def FloatProperty(**kwargs) -> Any:  # type: ignore[misc]
        return cast(Any, "PROPERTY")

    def IntProperty(**kwargs) -> Any:  # type: ignore[misc]
        return cast(Any, "PROPERTY")

    def StringProperty(**kwargs) -> Any:  # type: ignore[misc]
        return cast(Any, "PROPERTY")


# Import Dispatcher (Registry)
# We use relative import since we are inside the package
try:
    dispatcher = importlib.import_module(".dispatcher", __package__)
except ImportError:
    # Fallback for some weird Blender path contexts
    dispatcher = importlib.import_module("dispatcher")

# Data for Integration
# SECURITY: API keys must be set via Blender preferences or environment variables


class BlenderMCPServer:
    def __init__(
        self,
        host="localhost",
        port=9879,
        auth_token=None,
        max_active_clients=4,
        handshake_timeout=5.0,
        body_timeout=30.0,
        idle_timeout=300.0,
    ):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        self.AuthToken = auth_token
        self.InstanceId = str(uuid.uuid4())
        self.AuthEpoch = 0
        self.MaxActiveClients = max(1, int(max_active_clients))
        self.HandshakeTimeout = max(1.0, float(handshake_timeout))
        self.BodyTimeout = max(1.0, float(body_timeout))
        self.IdleTimeout = max(1.0, float(idle_timeout))
        self._ClientSlots = threading.BoundedSemaphore(self.MaxActiveClients)
        self._Clients = set()
        self._ClientsLock = threading.Lock()

    def start(self):
        if self.running:
            log_debug("[BlenderMCP:Server] Start ignored; already running")
            return True

        try:
            from .core.session import ValidateAuthToken
            from .core.filesystem_boundary import ConfigureFilesystemPolicy, FilesystemAccess
            from .core.security import ConfigureSecurityPolicy
            from .core.thread_safety import ThreadSafety, is_main_thread

            # Preference reads are Blender API operations. Reject worker-thread startup
            # before resolving any bpy-backed configuration, and reset ordinary
            # control-plane state to fail-closed defaults while startup is assembled.
            if not getattr(bpy, "is_mock", False) and not is_main_thread():
                raise RuntimeError("Server startup must run on Blender's main thread")
            ConfigureSecurityPolicy()
            ConfigureFilesystemPolicy()

            self.AuthToken = ValidateAuthToken(self.GetAuthToken())
            ReadRoot, WriteRoot, AllowOverwrite = self.GetFilesystemPreferences()
            FilesystemPolicy = ConfigureFilesystemPolicy(ReadRoot, WriteRoot, AllowOverwrite)
            SafeMode, RawCodeEnabled = self.GetSecurityPreferences()
            ConfigureSecurityPolicy(SafeMode, RawCodeEnabled)
            if FilesystemPolicy.HasRoot(FilesystemAccess.READ):
                log_debug("[BlenderMCP:Security] Filesystem read capability configured")
            if FilesystemPolicy.HasRoot(FilesystemAccess.WRITE):
                log_debug("[BlenderMCP:Security] Filesystem write capability configured")
            if not self.IsLoopbackHost(self.host):
                raise ValueError("Remote binding is disabled; use a loopback host")
            if not getattr(bpy, "is_mock", False) and not ThreadSafety().Start():
                raise RuntimeError("Blender main-thread command queue failed to start")
            self.InstanceId = str(uuid.uuid4())
            self.AuthEpoch += 1

            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.port = int(self.socket.getsockname()[1])
            self.socket.listen(self.MaxActiveClients)
            self.running = True

            # Start server thread
            self.server_thread = threading.Thread(
                target=self._server_loop,
                name="BlenderMCP-Listener",
            )
            self.server_thread.daemon = True
            self.server_thread.start()

            log_debug(f"[BlenderMCP:Server] Started on loopback port {self.port}")
            return True
        except Exception:
            log_debug("[BlenderMCP:Server] Start failed; inspect configuration")
            self.stop()
            return False

    def GetAuthToken(self):
        """Resolve a user-scoped credential without consulting Scene data."""
        if isinstance(self.AuthToken, str) and self.AuthToken.strip():
            return self.AuthToken.strip()

        try:
            Addon: Any = bpy.context.preferences.addons.get(__package__)
            if Addon:
                PreferenceToken = str(getattr(Addon.preferences, "auth_token", "") or "").strip()
                if PreferenceToken:
                    return PreferenceToken
        except (AttributeError, KeyError, TypeError):
            pass

        EnvironmentToken = os.environ.get("BLENDER_MCP_AUTH_TOKEN", "").strip()
        if EnvironmentToken:
            return EnvironmentToken
        return ""

    @staticmethod
    def GetFilesystemPreferences():
        """Read local filesystem authority before listener threads start."""
        try:
            Addon: Any = bpy.context.preferences.addons.get(__package__)
            if Addon:
                Preferences = Addon.preferences
                ReadValue = getattr(Preferences, "filesystem_read_root", "")
                WriteValue = getattr(Preferences, "filesystem_write_root", "")
                OverwriteValue = getattr(Preferences, "allow_filesystem_overwrite", False)
                ReadRoot = ReadValue.strip() if isinstance(ReadValue, str) else ""
                WriteRoot = WriteValue.strip() if isinstance(WriteValue, str) else ""
                AllowOverwrite = OverwriteValue if isinstance(OverwriteValue, bool) else False
                return ReadRoot, WriteRoot, AllowOverwrite
        except (AttributeError, KeyError, TypeError):
            pass
        return "", "", False

    @staticmethod
    def GetSecurityPreferences():
        """Read authorization preferences on Blender's main thread before listening."""
        try:
            Addon: Any = bpy.context.preferences.addons.get(__package__)
            if Addon:
                Preferences = Addon.preferences
                SafeValue = getattr(Preferences, "safe_mode", True)
                RawValue = getattr(Preferences, "raw_code_enabled", False)
                SafeMode = SafeValue if isinstance(SafeValue, bool) else True
                RawCodeEnabled = RawValue if isinstance(RawValue, bool) else False
                return SafeMode, RawCodeEnabled and not SafeMode
        except (AttributeError, KeyError, TypeError):
            pass
        return True, False

    @staticmethod
    def IsLoopbackHost(Host):
        """Accept literal loopback addresses and localhost only."""
        if Host == "localhost":
            return True
        try:
            return ipaddress.ip_address(Host).is_loopback
        except ValueError:
            return False

    def RotateAuthToken(self, AuthToken):
        """Revoke all current sessions and install a new credential."""
        from .core.session import ValidateAuthToken

        self.AuthToken = ValidateAuthToken(AuthToken)
        self.AuthEpoch += 1
        self.InstanceId = str(uuid.uuid4())
        self._CloseClients()
        log_debug("[BlenderMCP:Auth] Credential rotated; active sessions revoked")

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        self._CloseClients()

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        log_debug("[BlenderMCP:Server] Stopped")

    def _CloseClients(self):
        with self._ClientsLock:
            Clients = list(self._Clients)
        for Client in Clients:
            try:
                Client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                Client.close()
            except OSError:
                pass

    def _server_loop(self):
        """Accept only a bounded number of loopback clients."""
        log_debug("[BlenderMCP:Server] Listener thread started")
        if self.socket:
            self.socket.settimeout(1.0)

        while self.running and self.socket:
            try:
                Client, _Address = self.socket.accept()
                if not self._ClientSlots.acquire(blocking=False):
                    self._RejectClientLimit(Client)
                    continue
                with self._ClientsLock:
                    self._Clients.add(Client)
                ClientThread = threading.Thread(
                    target=self._HandleAcceptedClient,
                    args=(Client,),
                    name="BlenderMCP-Client",
                    daemon=True,
                )
                ClientThread.start()
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    break
                log_debug("[BlenderMCP:Server] Listener socket error")
                time.sleep(0.25)

        log_debug("[BlenderMCP:Server] Listener thread stopped")

    def _RejectClientLimit(self, Client):
        from .core import protocol
        from .core.session import BuildErrorEnvelope

        try:
            protocol.send_message(
                Client,
                BuildErrorEnvelope("RESOURCE_LIMIT", "Active client limit reached"),
            )
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            try:
                Client.close()
            except OSError:
                pass

    def _HandleAcceptedClient(self, Client):
        try:
            self._handle_client(Client)
        finally:
            with self._ClientsLock:
                self._Clients.discard(Client)
            self._ClientSlots.release()

    def _handle_client(self, client):
        """Authenticate a client before accepting any tool payload."""
        from .core import protocol
        from .core.session import (
            BuildEnvelope,
            BuildErrorEnvelope,
            BuildScopedRequestId,
            MessageType,
            PREAUTH_MAX_FRAME_BYTES,
            ServerSession,
            SessionError,
        )

        Session = ServerSession(self.AuthToken, self.InstanceId, self.AuthEpoch)
        log_debug(f"[BlenderMCP:Auth] Challenge issued session={Session.SessionId}")
        try:
            client.settimeout(self.BodyTimeout)
            protocol.send_message(client, Session.BuildChallenge())
            AuthEnvelope = protocol.recv_message(
                client,
                max_frame_bytes=PREAUTH_MAX_FRAME_BYTES,
                header_timeout=self.HandshakeTimeout,
                body_timeout=self.HandshakeTimeout,
            )
            if AuthEnvelope is None:
                raise SessionError(
                    "AUTH_REQUIRED",
                    "Authentication is required",
                    Session.RequestId,
                    Session.SessionId,
                )
            protocol.send_message(client, Session.Authenticate(AuthEnvelope))
            log_debug(f"[BlenderMCP:Auth] Authenticated session={Session.SessionId}")

            while self.running:
                Envelope = protocol.recv_message(
                    client,
                    header_timeout=self.IdleTimeout,
                    body_timeout=self.BodyTimeout,
                )
                if Envelope is None:
                    break
                Command = Session.ValidateRequest(Envelope)
                RequestId = Envelope["request_id"]
                ScopedRequestId = BuildScopedRequestId(Session.ClientInstanceId, RequestId)
                Command["request_id"] = ScopedRequestId
                Params = Command.get("params", {})
                if not isinstance(Params, dict):
                    raise SessionError(
                        "INVALID_REQUEST",
                        "Command params must be an object",
                        RequestId,
                        Session.SessionId,
                    )
                ExposedIds = {ScopedRequestId: RequestId}
                if Command.get("tool") == "manage_command_lifecycle" and Params.get("action") in {
                    "GET_STATUS",
                    "CANCEL",
                }:
                    TargetRequestId = Params.get("target_request_id")
                    if not isinstance(TargetRequestId, str):
                        raise SessionError(
                            "INVALID_REQUEST_ID",
                            "A bounded target_request_id is required",
                            RequestId,
                            Session.SessionId,
                        )
                    ScopedTargetId = BuildScopedRequestId(Session.ClientInstanceId, TargetRequestId)
                    Params = dict(Params)
                    Params["target_request_id"] = ScopedTargetId
                    Command["params"] = Params
                    ExposedIds[ScopedTargetId] = TargetRequestId
                RequestedTool = Command.get("tool")
                ToolName = (
                    RequestedTool
                    if isinstance(RequestedTool, str)
                    and RequestedTool in dispatcher.HANDLER_REGISTRY
                    else "unknown"
                )
                RequestedAction = Params.get("action")
                KnownActions = dispatcher.HANDLER_METADATA.get(ToolName, {}).get("actions", [])
                ActionName = (
                    RequestedAction
                    if isinstance(RequestedAction, str) and RequestedAction in KnownActions
                    else "unknown"
                )
                log_debug(
                    f"[BlenderMCP:Server] Dispatch request={RequestId} "
                    f"tool={ToolName} action={ActionName}"
                )
                Response = self.execute_command(Command)
                Response = self._ExposeRequestIds(Response, ExposedIds)
                protocol.send_message(
                    client,
                    BuildEnvelope(MessageType.RESPONSE, RequestId, Session.SessionId, Response),
                )
        except (SessionError, protocol.ProtocolError) as Error:
            log_debug(f"[BlenderMCP:Protocol] Rejected code={Error.Code}")
            RequestId = getattr(Error, "RequestId", Session.RequestId)
            SessionId = getattr(Error, "SessionId", Session.SessionId)
            try:
                protocol.send_message(
                    client,
                    BuildErrorEnvelope(Error.Code, Error.Message, RequestId, SessionId),
                )
            except (OSError, protocol.ProtocolError):
                pass
        except socket.timeout:
            log_debug("[BlenderMCP:Protocol] Connection deadline exceeded")
        except OSError:
            log_debug("[BlenderMCP:Protocol] Connection closed")
        finally:
            Session.Close()
            try:
                client.close()
            except OSError:
                pass

    @classmethod
    def _ExposeRequestIds(cls, Value, ExposedIds):
        """Replace internal ledger identities only in request-ID metadata fields."""
        if isinstance(Value, dict):
            Result = {}
            for Key, Item in Value.items():
                if Key in {"request_id", "target_request_id"} and isinstance(Item, str):
                    Result[Key] = ExposedIds.get(Item, Item)
                else:
                    Result[Key] = cls._ExposeRequestIds(Item, ExposedIds)
            return Result
        if isinstance(Value, list):
            return [cls._ExposeRequestIds(Item, ExposedIds) for Item in Value]
        return Value

    def execute_command(self, command):
        """Execute a command with detailed error handling"""
        try:
            result = self._execute_command_internal(command)
            return result
        except Exception:
            log_debug("[BlenderMCP:Dispatcher] Command execution failed")
            return {
                "status": "error",
                "error": {"code": "BLENDER_EXECUTION_ERROR", "message": "Command failed"},
            }

    def _execute_command_internal(self, command):
        """Internal command execution using modular dispatch system"""
        cmd_type = command.get("type") or command.get("tool")  # Support both formats
        command.get("params", {})

        # Normalize command structure for dispatcher
        if not command.get("tool"):
            command["tool"] = cmd_type

        # Use modular handler system
        try:
            # Dispatch command to registry
            # We pass self (ctx) to the dispatcher
            result = dispatcher.dispatch_command(command, ctx=self)

            # Check for error dict
            if isinstance(result, dict) and "error" in result:
                ErrorPayload = {
                    "code": result.get("code", "BLENDER_EXECUTION_ERROR"),
                    "message": str(result.get("error", "Command failed")),
                }
                for Field in ("command_state", "retry_safe", "terminal"):
                    if Field in result:
                        ErrorPayload[Field] = result[Field]
                return {
                    "status": "error",
                    "error": ErrorPayload,
                    "_meta": result.get("_meta", {}),
                }

            return {"status": "success", "result": result}

        except Exception:
            log_debug("[BlenderMCP:Dispatcher] Modular handler failed")
            return {
                "status": "error",
                "error": {"code": "BLENDER_EXECUTION_ERROR", "message": "Command failed"},
            }


class BLENDERMCP_OT_RotateAuthToken(bpy.types.Operator):
    bl_idname = "blendermcp.rotate_auth_token"
    bl_label = "Generate New Authentication Credential"
    bl_description = "Generate a new credential and revoke active MCP sessions"

    def execute(self, context):
        Addon = context.preferences.addons.get(__package__)
        if not Addon:
            log_debug("[BlenderMCP:Auth] Credential rotation failed; preferences unavailable")
            return {"CANCELLED"}

        NewToken = secrets.token_urlsafe(32)
        Addon.preferences.auth_token = NewToken
        Server = getattr(bpy.types, "blendermcp_server", None)
        if Server:
            Server.RotateAuthToken(NewToken)
        log_debug("[BlenderMCP:Auth] New user-scoped credential generated")
        return {"FINISHED"}


class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__  # Use package name for preferences

    auth_token: str = cast(
        str,
        StringProperty(
            name="Authentication Credential",
            description="User-scoped MCP credential; copy it to BLENDER_MCP_AUTH_TOKEN",
            default="",
            subtype="PASSWORD",
        ),
    )

    safe_mode: bool = cast(
        bool,
        BoolProperty(
            name="Safe Mode",
            description="Prevent arbitrary Python execution after the MCP server restarts",
            default=True,
        ),
    )

    raw_code_enabled: bool = cast(
        bool,
        BoolProperty(
            name="Allow Raw Python",
            description="Allow unrestricted Python after restart only while Safe Mode is off",
            default=False,
        ),
    )

    filesystem_read_root: str = cast(
        str,
        StringProperty(
            name="Filesystem Read Root",
            description="Existing local directory structured MCP tools may read after restart",
            default="",
            subtype="DIR_PATH",
        ),
    )

    filesystem_write_root: str = cast(
        str,
        StringProperty(
            name="Filesystem Write Root",
            description="Existing local directory structured MCP tools may write after restart",
            default="",
            subtype="DIR_PATH",
        ),
    )

    allow_filesystem_overwrite: bool = cast(
        bool,
        BoolProperty(
            name="Allow MCP File Overwrite",
            description="Allow structured MCP tools to replace files inside the write root",
            default=False,
        ),
    )

    def draw(self, context):
        layout = self.layout

        # Security Section
        layout.label(text="Security:", icon="LOCKED")
        box = layout.box()
        box.prop(self, "auth_token", text="Authentication Credential")
        box.operator("blendermcp.rotate_auth_token", icon="FILE_REFRESH")
        box.prop(self, "safe_mode", text="Safe Mode (Disable Python Execution)")
        box.prop(self, "raw_code_enabled", text="Allow Raw Python (High Risk)")
        box.separator()
        box.prop(self, "filesystem_read_root", text="Filesystem Read Root")
        box.prop(self, "filesystem_write_root", text="Filesystem Write Root")
        box.prop(self, "allow_filesystem_overwrite", text="Allow File Overwrite (High Risk)")
        box.label(text="Authorization and filesystem changes apply after restart.", icon="INFO")
        if self.safe_mode:
            box.label(text="Read-only audited actions are allowed.", icon="CHECKMARK")
        else:
            box.label(text="Structured mutations are allowed.", icon="ERROR")
        if self.raw_code_enabled and not self.safe_mode:
            box.label(text="Unrestricted Python is ALLOWED.", icon="ERROR")

        # Terms link
        box.separator()
        box.operator("blendermcp.open_terms", text="View Terms and Conditions", icon="TEXT")


# Blender UI Panel
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        if not layout:
            return
        scene = context.scene

        if scene:
            layout.prop(scene, "blendermcp_port")

        # Security Indicator
        prefs_addon = None
        if context.preferences and context.preferences.addons:
            prefs_addon = context.preferences.addons.get(__package__)

        if prefs_addon:
            prefs = prefs_addon.preferences
            if prefs.safe_mode:
                layout.label(text="Safe Mode: ON", icon="LOCKED")
            else:
                layout.label(text="Safe Mode: OFF", icon="ERROR")

        layout.separator()

        if scene:
            # Integration Toggles
            layout.prop(scene, "blendermcp_use_polyhaven", text="Poly Haven Integration")
            layout.prop(scene, "blendermcp_use_hyper3d", text="Hyper3D Rodin Integration")

            if scene.blendermcp_use_hyper3d:
                box = layout.box()
                if box:
                    box.prop(scene, "blendermcp_hyper3d_mode", text="Mode")
                    box.prop(scene, "blendermcp_hyper3d_api_key", text="API Key")

            layout.prop(scene, "blendermcp_use_sketchfab", text="Sketchfab Integration")
            if scene.blendermcp_use_sketchfab:
                layout.prop(scene, "blendermcp_sketchfab_api_key", text="API Key")

            layout.prop(scene, "blendermcp_use_hunyuan3d", text="Hunyuan3D Integration")

            if scene.blendermcp_use_hunyuan3d:
                box = layout.box()
                if box:
                    box.prop(scene, "blendermcp_hunyuan3d_mode", text="Mode")
                    if scene.blendermcp_hunyuan3d_mode == "OFFICIAL_API":
                        box.prop(scene, "blendermcp_hunyuan3d_secret_id", text="Secret ID")
                        box.prop(scene, "blendermcp_hunyuan3d_secret_key", text="Secret Key")
                    else:
                        box.prop(scene, "blendermcp_hunyuan3d_api_url", text="API URL")

                    box.prop(
                        scene,
                        "blendermcp_hunyuan3d_octree_resolution",
                        text="Octree Resolution",
                    )
                    box.prop(
                        scene,
                        "blendermcp_hunyuan3d_num_inference_steps",
                        text="Inference Steps",
                    )
                    box.prop(scene, "blendermcp_hunyuan3d_guidance_scale", text="Guidance Scale")
                    box.prop(scene, "blendermcp_hunyuan3d_texture", text="Generate Texture")

        layout.separator()

        if not scene.blendermcp_server_running:
            try:
                layout.operator(
                    "blendermcp.start_server", text="Connect to MCP server", icon="LINKED"
                )
            except:
                pass
        else:
            try:
                layout.operator("blendermcp.stop_server", text="Disconnect", icon="UNLINKED")
                layout.label(text=f"Running on port {scene.blendermcp_port}")
            except:
                pass


# Operator to start the server
class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Connect to Claude"
    bl_description = "Start the BlenderMCP server to connect with Claude"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "blendermcp_server") or not getattr(
            bpy.types, "blendermcp_server", None
        ):
            setattr(bpy.types, "blendermcp_server", BlenderMCPServer(port=scene.blendermcp_port))

        # Start the server
        server = getattr(bpy.types, "blendermcp_server", None)
        if server:
            server.start()
        # scene.blendermcp_server_running = True # This relies on UI update

        return {"FINISHED"}


# Operator to stop the server
class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the connection to Claude"
    bl_description = "Stop the connection to Claude"

    def execute(self, context):
        # Stop the server if it exists
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server

        # scene.blendermcp_server_running = False

        return {"FINISHED"}


# Operator to open Terms and Conditions
class BLENDERMCP_OT_OpenTerms(bpy.types.Operator):
    bl_idname = "blendermcp.open_terms"
    bl_label = "View Terms and Conditions"
    bl_description = "Open the Terms and Conditions document"

    def execute(self, context):
        terms_url = "https://github.com/glonorce/blender-mcp/blob/main/LICENSE"
        try:
            import webbrowser

            webbrowser.open(terms_url)
            self.report({"INFO"}, "Terms and Conditions opened in browser")
        except Exception as e:
            self.report({"ERROR"}, f"Could not open Terms and Conditions: {str(e)}")

        return {"FINISHED"}


class BLENDERMCP_OT_DebugTools(bpy.types.Operator):
    bl_idname = "blendermcp.debug_tools"
    bl_label = "Debug Tools List"
    bl_description = "Print list of registered tools to console"

    def execute(self, context):
        tools = dispatcher.list_all_tools()
        print("[MCP] Registered Tools:")
        print(json.dumps(tools, indent=2))
        self.report({"INFO"}, f"Logged {tools['count']} tools to console")
        return {"FINISHED"}


classes = (
    BLENDERMCP_OT_RotateAuthToken,
    BLENDERMCP_OT_StartServer,
    BLENDERMCP_OT_StopServer,
    BLENDERMCP_OT_OpenTerms,
    BLENDERMCP_OT_DebugTools,
    BLENDERMCP_AddonPreferences,
    BLENDERMCP_PT_Panel,
)


def register():
    """Register the Blender MCP add-on."""
    log_debug("[MCP] Registering Blender MCP add-on...")
    print("[MCP] Registering Blender MCP add-on 1.0.0...")

    try:
        # Load modular handlers with detailed logging
        try:
            log_debug("[MCP] Starting handler loading...")
            print("[MCP] Loading handlers...")
            dispatcher.load_handlers()
            handler_count = len(dispatcher.HANDLER_REGISTRY)
            log_debug(f"[MCP] Loaded {handler_count} handlers")
            print(f"[MCP] Successfully loaded {handler_count} handlers")
        except Exception as e:
            error_detail = traceback.format_exc()
            log_debug(f"[MCP] Failed to load handlers: {e}")
            log_debug(f"[MCP] Traceback: {error_detail}")
            print(f"[MCP] ERROR: Handler loading failed: {e}")
            print(error_detail)

        # Register classes
        for cls in classes:
            try:
                bpy.utils.register_class(cls)
            except Exception as e:
                log_debug(f"[MCP] Failed to register class {cls.__name__}: {e}")
                raise  # Re-raise to prevent partial registration

        # Properties - with safe deletion first (in case of reload)
        properties_to_add = [
            (
                "blendermcp_port",
                IntProperty(  # type: ignore[func-returns-value]
                    name="Port",
                    description="Port for the BlenderMCP server",
                    default=9879,
                    min=1024,
                    max=65535,
                    subtype="UNSIGNED",
                ),
            ),
            (
                "blendermcp_server_running",
                BoolProperty(  # type: ignore[func-returns-value]
                    name="Server Running",
                    get=lambda self: (
                        hasattr(bpy.types, "blendermcp_server")
                        and bpy.types.blendermcp_server
                        and bpy.types.blendermcp_server.running
                    ),
                ),
            ),
            # Integration Properties
            (
                "blendermcp_use_polyhaven",
                BoolProperty(name="Use assets from Poly Haven", default=False),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_use_sketchfab",
                BoolProperty(name="Use assets from Sketchfab", default=False),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_sketchfab_api_key",
                StringProperty(name="API Key", default="", subtype="PASSWORD"),  # type: ignore[func-returns-value]
            ),
            ("blendermcp_use_hyper3d", BoolProperty(name="Use Hyper3D", default=False)),  # type: ignore[func-returns-value]
            (
                "blendermcp_hyper3d_mode",
                EnumProperty(  # type: ignore[func-returns-value]
                    items=[("MAIN_SITE", "Main Site", ""), ("FAL_AI", "Fal AI", "")],
                    name="Mode",
                    default="MAIN_SITE",
                ),
            ),
            (
                "blendermcp_hyper3d_api_key",
                StringProperty(name="API Key", default="", subtype="PASSWORD"),  # type: ignore[func-returns-value]
            ),
            ("blendermcp_use_hunyuan3d", BoolProperty(name="Use Hunyuan3D", default=False)),  # type: ignore[func-returns-value]
            (
                "blendermcp_hunyuan3d_mode",
                EnumProperty(  # type: ignore[func-returns-value]
                    items=[("OFFICIAL_API", "Official API", ""), ("LOCAL_API", "Local API", "")],
                    name="Mode",
                    default="OFFICIAL_API",
                ),
            ),
            (
                "blendermcp_hunyuan3d_secret_id",
                StringProperty(name="Secret ID", default="", subtype="PASSWORD"),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_hunyuan3d_secret_key",
                StringProperty(name="Secret Key", default="", subtype="PASSWORD"),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_hunyuan3d_api_url",
                StringProperty(name="API URL", default="http://127.0.0.1:8080"),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_hunyuan3d_octree_resolution",
                IntProperty(name="Octree Resolution", default=256),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_hunyuan3d_num_inference_steps",
                IntProperty(name="Number of Inference Steps", default=50),  # type: ignore[func-returns-value]
            ),
            (
                "blendermcp_hunyuan3d_guidance_scale",
                FloatProperty(name="Guidance Scale", default=5.0),  # type: ignore[func-returns-value]
            ),
            ("blendermcp_hunyuan3d_texture", BoolProperty(name="Generate Texture", default=True)),  # type: ignore[func-returns-value]
        ]

        for prop_name, prop_value in properties_to_add:
            try:
                # Remove if exists (for reload safety)
                if hasattr(bpy.types.Scene, prop_name):
                    delattr(bpy.types.Scene, prop_name)
                setattr(bpy.types.Scene, prop_name, prop_value)
            except Exception as e:
                log_debug(f"[MCP] Failed to add property {prop_name}: {e}")
                raise

        log_debug("[MCP] Blender MCP add-on registered successfully")

    except Exception as e:
        log_debug(f"[MCP] Critical error during registration: {e}")
        log_debug(traceback.format_exc())
        raise  # Re-raise to signal Blender that registration failed


def unregister():
    """Unregister the Blender MCP add-on."""
    log_debug("[MCP] Unregistering Blender MCP add-on...")

    try:
        # Stop ThreadSafety monitor thread and remove depsgraph hook
        try:
            from blender_mcp.core.thread_safety import ThreadSafety

            ts = ThreadSafety._instance
            if ts is not None:
                ts.Shutdown()
            # Remove MCP depsgraph hook
            hooks = bpy.app.handlers.depsgraph_update_post
            hooks[:] = [h for h in hooks if getattr(h, "__name__", "") != "_mcp_depsgraph_hook"]
        except Exception as _e:
            log_debug(f"[MCP] ThreadSafety cleanup error: {_e}")

        # Stop server first
        if hasattr(bpy.types, "blendermcp_server") and getattr(
            bpy.types, "blendermcp_server", None
        ):
            try:
                getattr(bpy.types, "blendermcp_server").stop()
                log_debug("[MCP] Server stopped")
            except Exception as e:
                log_debug(f"[MCP] Error stopping server: {e}")
            finally:
                try:
                    delattr(bpy.types, "blendermcp_server")
                except:
                    pass

        # Unregister classes
        for cls in reversed(classes):
            try:
                bpy.utils.unregister_class(cls)  # type: ignore[arg-type]
            except Exception as e:
                log_debug(f"[MCP] Error unregistering class {cls.__name__}: {e}")

        # Remove properties
        properties_to_remove = [
            "blendermcp_port",
            "blendermcp_server_running",
            "blendermcp_use_polyhaven",
            "blendermcp_use_sketchfab",
            "blendermcp_sketchfab_api_key",
            "blendermcp_use_hyper3d",
            "blendermcp_hyper3d_mode",
            "blendermcp_hyper3d_api_key",
            "blendermcp_use_hunyuan3d",
            "blendermcp_hunyuan3d_mode",
            "blendermcp_hunyuan3d_secret_id",
            "blendermcp_hunyuan3d_secret_key",
            "blendermcp_hunyuan3d_api_url",
            "blendermcp_hunyuan3d_octree_resolution",
            "blendermcp_hunyuan3d_num_inference_steps",
            "blendermcp_hunyuan3d_guidance_scale",
            "blendermcp_hunyuan3d_texture",
        ]

        for prop in properties_to_remove:
            try:
                if hasattr(bpy.types.Scene, prop):
                    delattr(bpy.types.Scene, prop)
            except Exception as e:
                log_debug(f"[MCP] Error removing property {prop}: {e}")

        log_debug("[MCP] Blender MCP add-on unregistered successfully")

    except Exception as e:
        log_debug(f"[MCP] Error during unregistration: {e}")
        log_debug(traceback.format_exc())


if __name__ == "__main__":
    register()
