import sys
import io
import ipaddress
import json
import socket
import logging
import os
import tempfile
import threading
import time
import uuid
import jsonschema
from jsonschema.exceptions import ValidationError
from typing import Any, cast, Dict, Optional

# Force UTF-8 for MCP communication (Crucial for Windows)
if sys.platform == "win32":
    if hasattr(sys.stdin, "reconfigure") and hasattr(sys.stdout, "reconfigure"):
        try:
            cast(Any, sys.stdin).reconfigure(encoding="utf-8")
            cast(Any, sys.stdout).reconfigure(encoding="utf-8")
        except AttributeError:
            # Fallback for older python or restricted envs
            sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Configure logging - STAFF+ DEBUGGING (Absolute Path)
log_file = os.path.join(tempfile.gettempdir(), "mcp_bridge_debug.log")
logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.info("[BlenderMCP:Bridge] Started")

# HARDENING: Ensure we can import the local package
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
# Also add parent if needed, but current_dir contains 'blender_mcp' folder so it should be enough.


class MCPBridge:
    def __init__(self, host="localhost", port=9879, auth_token=None):
        self.host = host
        self.port = port
        self.client_socket = None
        self.AuthToken = auth_token or os.environ.get("BLENDER_MCP_AUTH_TOKEN", "")
        self.Session = None
        self._TransactionLock = threading.RLock()
        self._LastErrorCode = ""

        # Schema Cache for Dynamic Validation
        self._tool_schemas: Dict[str, dict] = {}
        self._tool_descriptions: Dict[str, str] = {}
        self._schemas_loaded = False

    def connect(self):
        """Establish and authenticate a connection to Blender."""
        from blender_mcp.core.session import ClientSession, SessionError

        self.CloseConnection()
        if not self.IsLoopbackHost(self.host):
            self._LastErrorCode = "REMOTE_HOST_DISABLED"
            logging.error("[BlenderMCP:Bridge] Remote host rejected")
            return False
        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.settimeout(5.0)
            self.client_socket.connect((self.host, self.port))
            self.Session = ClientSession(self.AuthToken)
            self.Session.PerformHandshake(self.client_socket)
            self._LastErrorCode = ""
            logging.info("[BlenderMCP:Auth] Authenticated local session")
            return True
        except SessionError as Error:
            self._LastErrorCode = Error.Code
            logging.error(f"[BlenderMCP:Auth] Connection rejected code={Error.Code}")
            self.CloseConnection()
            return False
        except OSError:
            self._LastErrorCode = "CONNECTION_FAILED"
            logging.error("[BlenderMCP:Bridge] Connection failed")
            self.CloseConnection()
            return False

    @staticmethod
    def IsLoopbackHost(Host):
        """Protocol v1 never sends authentication material to a remote host."""
        if Host == "localhost":
            return True
        try:
            return ipaddress.ip_address(Host).is_loopback
        except ValueError:
            return False

    def send_to_blender(self, command_dict, retries=3):
        """Send one correlated transaction without replaying ambiguous work."""
        from blender_mcp.core import protocol
        from blender_mcp.core.session import NormalizeRequestId, SessionError

        RequestId = NormalizeRequestId(command_dict.get("request_id") or str(uuid.uuid4()))
        Command = dict(command_dict)
        Command["request_id"] = RequestId

        with self._TransactionLock:
            AttemptsRemaining = max(0, int(retries)) + 1
            while not self.client_socket or not self.Session:
                if self.connect():
                    break
                AttemptsRemaining -= 1
                if AttemptsRemaining <= 0:
                    return {
                        "error": "Could not establish an authenticated Blender session",
                        "code": self._LastErrorCode or "CONNECTION_FAILED",
                        "request_id": RequestId,
                    }
                time.sleep(0.25)

            TransmissionStarted = False
            try:
                Envelope = self.Session.BuildRequest(RequestId, Command)
                TransmissionStarted = True
                protocol.send_message(self.client_socket, Envelope)
                ResponseEnvelope = protocol.recv_message(
                    self.client_socket,
                    header_timeout=360.0,
                    body_timeout=360.0,
                )
                if ResponseEnvelope is None:
                    raise SessionError(
                        "CONNECTION_CLOSED",
                        "Connection closed before a correlated response",
                        RequestId,
                    )
                Response = self.Session.ValidateResponse(ResponseEnvelope, RequestId)
                logging.info(f"[BlenderMCP:Bridge] Response received request={RequestId}")
                return Response
            except (socket.timeout, OSError, protocol.ProtocolError, SessionError) as Error:
                ErrorCode = (
                    "REQUEST_INDETERMINATE"
                    if TransmissionStarted
                    else getattr(Error, "Code", "TRANSPORT_ERROR")
                )
                self.CloseConnection()
                logging.error(
                    f"[BlenderMCP:Bridge] Transaction failed request={RequestId} code={ErrorCode}"
                )
                return {
                    "error": (
                        "Request outcome is indeterminate; reconnect and reconcile this request_id"
                        if TransmissionStarted
                        else "Request was not sent"
                    ),
                    "code": ErrorCode,
                    "request_id": RequestId,
                }

    def CloseConnection(self):
        """Close and forget a connection so late frames cannot cross requests."""
        if self.Session:
            self.Session.Close()
        self.Session = None
        if self.client_socket:
            try:
                self.client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.client_socket.close()
            except OSError:
                pass
        self.client_socket = None

    def _sanitize_schema(self, schema):
        """Ensure schema is a valid JSON Schema object (Staff+ Hardening)"""
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}, "additionalProperties": False}

        # Ensure mandatory 'type': 'object'
        if schema.get("type") != "object":
            # If it's empty or missing type, force it.
            # Warning: checks for 'inputSchema' usually expect an object root.
            schema["type"] = "object"

        if "properties" not in schema:
            schema["properties"] = {}

        return schema

    def _ensure_schemas_cache(self) -> Optional[Dict[str, Any]]:
        """Lazy load or refresh schemas from Blender. Returns error dict if failed."""
        blender_resp = self.send_to_blender(
            {"tool": "list_all_tools", "params": {"action": "list_all_tools"}}
        )

        if not blender_resp or "error" in blender_resp:
            ErrorCode = (
                blender_resp.get("code", "CONNECTION_FAILED")
                if blender_resp
                else "CONNECTION_FAILED"
            )
            logging.error(f"[BlenderMCP:Bridge] Tool discovery failed code={ErrorCode}")
            return {
                "error": {
                    "code": -32603,
                    "message": f"Blender connection failed ({ErrorCode})",
                }
            }

        if blender_resp.get("status") == "error":
            ErrorValue = blender_resp.get("error", {})
            ErrorCode = (
                ErrorValue.get("code", "BLENDER_EXECUTION_ERROR")
                if isinstance(ErrorValue, dict)
                else "BLENDER_EXECUTION_ERROR"
            )
            logging.error(f"[BlenderMCP:Bridge] Tool discovery rejected code={ErrorCode}")
            return {
                "error": {
                    "code": -32603,
                    "message": f"Blender rejected tool discovery ({ErrorCode})",
                }
            }

        if "result" in blender_resp and "tools" in blender_resp["result"]:
            self._tool_schemas.clear()
            self._tool_descriptions.clear()
            for tool_meta in blender_resp["result"]["tools"]:
                name = tool_meta.get("name")
                if not name:
                    continue
                schema = self._sanitize_schema(tool_meta.get("schema"))
                self._tool_schemas[name] = schema
                self._tool_descriptions[name] = tool_meta.get("description", "")

            self._schemas_loaded = True
            logging.info(f"[BlenderMCP:Bridge] Loaded {len(self._tool_schemas)} tool schemas")

        return None

    def run_stdio_loop(self):
        """Main Loop: Read Stdin (MCP JSON-RPC) -> Process -> Write Stdout"""
        logging.info("Starting Stdio Bridge Loop")

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                request = json.loads(line)
                response = self.handle_mcp_request(request)

                if response:
                    sys.stdout.write(json.dumps(response) + "\n")
                    sys.stdout.flush()

            except json.JSONDecodeError:
                continue
            except Exception:
                logging.error("[BlenderMCP:Bridge] Stdio loop error")

    def handle_mcp_request(self, request):
        """Route MCP JSON-RPC requests"""
        msg_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        from blender_mcp.core.session import NormalizeRequestId

        WireRequestId = NormalizeRequestId(msg_id if msg_id is not None else str(uuid.uuid4()))

        response = {"jsonrpc": "2.0", "id": msg_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "Blender MCP Bridge", "version": "1.0.0"},
            }
            return response

        elif method == "notifications/initialized":
            # No response needed for notifications
            return None

        elif method == "ping":
            return None  # Ping might not need response in all implementations, or just empty result

        elif method == "tools/list":
            # Always refresh cache on tools/list to adapt to active Blender changes
            err_resp = self._ensure_schemas_cache()

            if err_resp:
                response["error"] = err_resp["error"]
                return response

            mcp_tools = []
            for name, schema in self._tool_schemas.items():
                mcp_tools.append(
                    {
                        "name": name,
                        "description": self._tool_descriptions.get(name, ""),
                        "inputSchema": schema,
                    }
                )

            response["result"] = {"tools": mcp_tools}
            return response

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            # DYNAMIC VALIDATION GUARD LAYER (Risk Mitigation: Invalid Enum/Args)
            if not self._schemas_loaded:
                # Lazy load if a call arrives before list
                err_resp = self._ensure_schemas_cache()
                if err_resp:
                    response["result"] = {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: Cannot validate schema: {err_resp['error']['message']}.",
                            }
                        ],
                        "isError": True,
                    }
                    return response

            if tool_name in self._tool_schemas:
                try:
                    jsonschema.validate(instance=tool_args, schema=self._tool_schemas[tool_name])
                except ValidationError:
                    logging.warning(
                        f"[BlenderMCP:Bridge] Schema validation failed tool={tool_name}"
                    )
                    response["result"] = {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: Schema validation failed.",
                            }
                        ],
                        "isError": True,
                    }
                    return response
            else:
                response["result"] = {
                    "content": [{"type": "text", "text": "Error: Unknown tool."}],
                    "isError": True,
                }
                return response

            # Forward to Blender
            blender_resp = self.send_to_blender(
                {"tool": tool_name, "params": tool_args, "request_id": WireRequestId}
            )

            if blender_resp.get("status") == "success":
                content = []
                result_data = blender_resp.get("result", {})

                # SPECIAL HANDLING: IMAGE CONTENT
                # ResponseBuilder wraps data as: {"status":"OK","data":{...},...}
                # so __mcp_image_data__ lives at result_data["data"], not result_data top-level.
                # We check both locations for backwards-compatibility.
                def _extract_image(payload: dict) -> None:
                    if "__mcp_image_data__" in payload:
                        img_data = payload.pop("__mcp_image_data__")
                        mime_type = payload.pop("__mcp_image_mime__", "image/png")
                        content.append({"type": "image", "data": img_data, "mimeType": mime_type})
                    # Multi-image list: __mcp_images__ = [{data, mime, label}, ...]
                    if "__mcp_images__" in payload:
                        for img in payload.pop("__mcp_images__", []):
                            if isinstance(img, dict) and img.get("data"):
                                content.append(
                                    {
                                        "type": "image",
                                        "data": img["data"],
                                        "mimeType": img.get("mime", "image/png"),
                                    }
                                )

                if isinstance(result_data, dict):
                    # Primary location: nested inside result_data["data"] (ResponseBuilder layout)
                    data_payload = result_data.get("data")
                    if isinstance(data_payload, dict):
                        _extract_image(data_payload)
                    # Fallback: top-level (direct placement)
                    if not content:
                        _extract_image(result_data)

                # Add Text Content (Remaining data)
                content.append({"type": "text", "text": json.dumps(result_data, indent=2)})

                response["result"] = {"content": content, "isError": False}
            else:
                ErrorValue = blender_resp.get("error", "Unknown error")
                if isinstance(ErrorValue, dict):
                    ErrorMessage = ErrorValue.get("message", "Unknown error")
                else:
                    ErrorMessage = ErrorValue
                response["result"] = {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: {ErrorMessage}",
                        }
                    ],
                    "isError": True,
                }
            return response

        else:
            # Method not found
            # For robustness, we mostly ignore unknown methods or return null result to avoid crashing client
            # But proper RPC returns error object.
            # keeping it simple for now.
            return None


if __name__ == "__main__":
    Host = os.environ.get("BLENDER_HOST", "localhost")
    try:
        Port = int(os.environ.get("BLENDER_PORT", "9879"))
    except ValueError:
        Port = 9879
        logging.error("[BlenderMCP:Bridge] Invalid BLENDER_PORT; using 9879")
    bridge = MCPBridge(host=Host, port=Port)
    bridge.run_stdio_loop()
