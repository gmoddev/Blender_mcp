"""
Deterministic Execution Tests for MCP Bridge 1.0.0

Tests the Dynamic Validation (jsonschema) layer implemented in stdio_bridge.py.
Focuses on ensuring predictability against 1x valid and 2x invalid (wrong enum, missing param) inputs.

Also includes pattern-scan tests for execute_blender_code blocking rules.
"""

import re
import pytest
from unittest.mock import MagicMock

# Adjust path to find stdio_bridge and blender_mcp
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from stdio_bridge import MCPBridge
from blender_mcp.core.session import NormalizeJsonRpcRequestId


@pytest.fixture
def mock_bridge():
    """Returns an MCPBridge instance with a mocked Blender connection."""
    bridge = MCPBridge()
    # Mock send_to_blender instead of actual socket communication
    bridge.send_to_blender = MagicMock()
    return bridge


def setup_dummy_tools(mock_bridge):
    """Mocks the tools/list response from Blender."""
    mock_bridge.send_to_blender.return_value = {
        "status": "success",
        "result": {
            "tools": [
                {
                    "name": "dummy_tool",
                    "description": "A deterministically testable dummy tool",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["START", "STOP"]},
                            "speed": {"type": "number", "minimum": 0},
                        },
                        "required": ["action", "speed"],
                    },
                }
            ]
        },
    }
    # Force the _ensure_schemas_cache load
    mock_bridge._schemas_loaded = False


def test_validation_success_path(mock_bridge):
    """Test 1: Valid input should pass the schema check and forward to Blender."""
    setup_dummy_tools(mock_bridge)

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "dummy_tool", "arguments": {"action": "START", "speed": 50}},
    }

    # Run the handler
    mock_bridge.handle_mcp_request(request)

    # In a valid scenario, send_to_blender should have been called TWICE:
    # 1st for tools/list (lazy load via _ensure_schemas_cache)
    # 2nd for the actual tool call
    assert mock_bridge.send_to_blender.call_count == 2

    # Check the 2nd call arguments
    args, kwargs = mock_bridge.send_to_blender.call_args
    assert args[0] == {
        "tool": "dummy_tool",
        "params": {"action": "START", "speed": 50},
        "request_id": NormalizeJsonRpcRequestId(1),
    }


def test_json_rpc_numeric_and_string_ids_forward_as_distinct_wire_ids(mock_bridge):
    setup_dummy_tools(mock_bridge)
    mock_bridge.handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "dummy_tool", "arguments": {"action": "START", "speed": 1}},
        }
    )
    NumericId = mock_bridge.send_to_blender.call_args.args[0]["request_id"]
    mock_bridge.handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {"name": "dummy_tool", "arguments": {"action": "START", "speed": 1}},
        }
    )
    StringId = mock_bridge.send_to_blender.call_args.args[0]["request_id"]

    assert NumericId != StringId


def test_validation_invalid_enum(mock_bridge):
    """Test 2: Invalid Enum input should fail validation without reaching Blender logic."""
    setup_dummy_tools(mock_bridge)

    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "dummy_tool",
            "arguments": {
                "action": "PAUSE",  # PAUSE is not in ["START", "STOP"]
                "speed": 50,
            },
        },
    }

    response = mock_bridge.handle_mcp_request(request)

    # send_to_blender should be called ONCE (for the tools/list lazy cache),
    # but NOT for the tool call.
    assert mock_bridge.send_to_blender.call_count == 1

    assert response["result"]["isError"] is True
    ValidationText = response["result"]["content"][0]["text"]
    assert ValidationText == "Error: Schema validation failed."
    assert "PAUSE" not in ValidationText


def test_validation_missing_param(mock_bridge):
    """Test 3: Missing required param should fail validation without reaching Blender logic."""
    setup_dummy_tools(mock_bridge)

    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "dummy_tool",
            "arguments": {
                "action": "START"
                # Missing "speed"
            },
        },
    }

    response = mock_bridge.handle_mcp_request(request)

    assert mock_bridge.send_to_blender.call_count == 1

    assert response["result"]["isError"] is True
    ValidationText = response["result"]["content"][0]["text"]
    assert ValidationText == "Error: Schema validation failed."
    assert "speed" not in ValidationText


def test_bridge_preserves_lifecycle_error_details(mock_bridge):
    mock_bridge._schemas_loaded = True
    mock_bridge._tool_schemas = {
        "dummy_tool": {
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
        }
    }
    mock_bridge.send_to_blender.return_value = {
        "status": "error",
        "error": {
            "code": "REQUEST_INDETERMINATE",
            "message": "Reconcile before retry",
            "command_state": "running_after_timeout",
            "retry_safe": False,
            "terminal": False,
        },
        "_meta": {
            "request_id": "bridge-lifecycle",
            "command_state": "running_after_timeout",
        },
    }
    Request = {
        "jsonrpc": "2.0",
        "id": "bridge-lifecycle",
        "method": "tools/call",
        "params": {"name": "dummy_tool", "arguments": {"action": "DO"}},
    }

    Response = mock_bridge.handle_mcp_request(Request)
    ErrorText = Response["result"]["content"][0]["text"]

    assert Response["result"]["isError"] is True
    assert "REQUEST_INDETERMINATE" in ErrorText
    assert "running_after_timeout" in ErrorText
    assert "bridge-lifecycle" in ErrorText


# =============================================================================
# execute_blender_code blocking pattern tests (no bpy needed — pure regex scan)
# =============================================================================

# Replicate the patterns from manage_scripting.py to test them independently
_BLOCKING_RENDER_PATTERN = re.compile(r"bpy\.ops\.render\.render\s*\(")


def _check_code_blocked(code: str) -> bool:
    """Returns True if code would be blocked by the BLOCKED_PATTERN scan."""
    return bool(_BLOCKING_RENDER_PATTERN.search(code))


def test_render_render_direct_is_blocked() -> None:
    """bpy.ops.render.render() must be caught by the blocking pattern."""
    assert _check_code_blocked("bpy.ops.render.render()") is True


def test_render_render_with_args_is_blocked() -> None:
    """bpy.ops.render.render(write_still=True) must also be caught."""
    assert _check_code_blocked("bpy.ops.render.render(write_still=True)") is True


def test_render_render_with_spaces_is_blocked() -> None:
    """bpy.ops.render.render  () with extra spaces must be caught."""
    assert _check_code_blocked("bpy.ops.render.render  ()") is True


def test_render_render_in_string_import_pattern() -> None:
    """Verify the regex pattern matches the exact string from manage_scripting.py."""
    # This documents the exact pattern used in production
    pattern = r"bpy\.ops\.render\.render\s*\("
    assert re.search(pattern, "result = bpy.ops.render.render(write_still=True)") is not None


def test_normal_code_not_blocked() -> None:
    """Normal bpy code must NOT be blocked by the render pattern."""
    safe_codes = [
        "bpy.data.objects.new('Cube', None)",
        "import bpy\nobj = bpy.context.active_object",
        "bpy.ops.mesh.primitive_cube_add()",
        "print(bpy.app.version)",
    ]
    for code in safe_codes:
        assert _check_code_blocked(code) is False, f"Should not be blocked: {code!r}"
