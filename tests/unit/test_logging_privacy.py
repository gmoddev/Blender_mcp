"""Canary tests for metadata-only execution logging."""

from __future__ import annotations

import json
import logging
import sys
from unittest.mock import patch

from blender_mcp.core.logging_config import JSONFormatter, MCPLogger


def test_tool_logging_drops_parameters_results_and_exception_text() -> None:
    Canary = "CANARY-SECRET-CODE-PROMPT-SIGNED-URL"
    Logger = MCPLogger()
    with patch.object(Logger._logger, "error") as ErrorLog:
        Logger.log_tool_execution(
            tool="execute_blender_code",
            action="execute_blender_code",
            params={"nested": {"secret": Canary}, "code": Canary, "path": Canary},
            result={"provider_response": Canary},
            duration_ms=12.5,
            error=RuntimeError(Canary),
        )

    RenderedCall = repr(ErrorLog.call_args)
    assert Canary not in RenderedCall
    assert "params" not in ErrorLog.call_args.kwargs["extra"]
    assert "result" not in ErrorLog.call_args.kwargs["extra"]
    assert ErrorLog.call_args.kwargs["exc_info"] is False


def test_success_logging_contains_only_allowlisted_execution_metadata() -> None:
    Logger = MCPLogger()
    with patch.object(Logger._logger, "info") as InfoLog:
        Logger.log_tool_execution(
            tool="manage_scene",
            action="RENAME",
            params={"name": "private-scene-name"},
            result={"path": "private-path"},
            duration_ms=5.0,
        )

    Extra = InfoLog.call_args.kwargs["extra"]
    assert Extra["tool"] == "manage_scene"
    assert Extra["action"] == "RENAME"
    assert Extra["duration_ms"] == 5.0
    assert Extra["outcome"] == "success"
    assert set(Extra).issubset({"tool", "action", "duration_ms", "outcome", "request_id"})


def test_json_formatter_drops_sensitive_extras_and_exception_details() -> None:
    Canary = "CANARY-PRIVATE-PAYLOAD"
    try:
        raise RuntimeError(Canary)
    except RuntimeError:
        ErrorInfo = sys.exc_info()

    Record = logging.LogRecord(
        "blender_mcp",
        logging.ERROR,
        __file__,
        1,
        "[BlenderMCP:Test] Failed: %s",
        (Canary,),
        ErrorInfo,
    )
    Record.params = {"code": Canary}
    Record.result = {"response": Canary}
    Record.scene = Canary
    Record.provider_response = Canary

    Rendered = JSONFormatter().format(Record)
    Parsed = json.loads(Rendered)
    assert Canary not in Rendered
    assert Parsed["exception"] == {"type": "RuntimeError"}
    assert "params" not in Parsed
    assert "result" not in Parsed
    assert "scene" not in Parsed
    assert "provider_response" not in Parsed
