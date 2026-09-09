"""Reconcile and cancel retained main-thread command requests."""

from typing import Any, Dict

from ..core.security import Capability
from ..core.thread_safety import ThreadSafety
from ..dispatcher import register_handler


@register_handler(
    "manage_command_lifecycle",
    actions=["GET_STATUS", "CANCEL"],
    category="system",
    priority=7,
    capabilities={
        "GET_STATUS": [Capability.READ.value],
        "CANCEL": [Capability.MUTATE.value],
    },
    requires_main_thread=False,
    schema={
        "type": "object",
        "title": "Command Lifecycle Reconciliation",
        "description": (
            "Query a retained command result before retrying an indeterminate request, "
            "or cancel a command that is still pending. Running commands are never "
            "misreported as cancelled."
        ),
        "properties": {
            "action": {
                "type": "string",
                "enum": ["GET_STATUS", "CANCEL"],
            },
            "target_request_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Wire request ID returned by the original command.",
            },
            "include_result": {
                "type": "boolean",
                "default": True,
                "description": "Include a retained successful result in GET_STATUS.",
            },
        },
        "required": ["action", "target_request_id"],
    },
)
def manage_command_lifecycle(action: str, **params: Any) -> Dict[str, Any]:
    """Read or tombstone command state without waiting for Blender's main thread."""
    RequestId = str(params["target_request_id"])
    Lifecycle = ThreadSafety()

    if action == "GET_STATUS":
        Snapshot = Lifecycle.GetRequestStatus(
            RequestId,
            IncludeResult=bool(params.get("include_result", True)),
        )
    else:
        Snapshot = Lifecycle.CancelRequest(RequestId)

    if Snapshot is None:
        return {
            "error": "Request is unknown or no longer retained",
            "code": "REQUEST_NOT_FOUND",
            "target_request_id": RequestId,
            "retry_safe": False,
        }

    return {"request": Snapshot}
