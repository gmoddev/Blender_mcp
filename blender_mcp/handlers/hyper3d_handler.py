"""Security boundary for the optional Hyper3D integration."""

from typing import Any, Dict, Optional

import bpy

from ..core.enums import Hyper3DAction
from ..core.parameter_validator import validated_handler
from ..core.security import Capability
from ..core.thread_safety import ensure_main_thread
from ..core.validation_utils import ValidationUtils
from ..dispatcher import register_handler


for LegacyUnsafeName in (
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
):
    globals().pop(LegacyUnsafeName, None)
del LegacyUnsafeName


Hyper3DCapabilities = {
    Hyper3DAction.STATUS.value: [Capability.READ.value],
    Hyper3DAction.GENERATE.value: [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
    ],
    Hyper3DAction.CHECK_JOB.value: [
        Capability.READ.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
    ],
    Hyper3DAction.IMPORT.value: [
        Capability.MUTATE.value,
        Capability.NETWORK.value,
        Capability.FILESYSTEM_READ.value,
        Capability.FILESYSTEM_WRITE.value,
    ],
}
ExternalActions = frozenset(
    Action.value for Action in Hyper3DAction if Action is not Hyper3DAction.STATUS
)
ExternalCapabilityMessage = (
    "Hyper3D external actions are disabled until purpose-scoped network, credential, "
    "filesystem, download, content-validation, and provider job controls are available"
)


@register_handler(
    "integration_hyper3d",
    actions=[Action.value for Action in Hyper3DAction],
    capabilities=Hyper3DCapabilities,
    schema={
        "type": "object",
        "title": "Hyper3D Integration",
        "description": (
            "Inspect Hyper3D configuration. Generation, polling, and import actions are "
            "quarantined by the security policy."
        ),
        "properties": {
            "action": ValidationUtils.generate_enum_schema(Hyper3DAction, "Operation to perform"),
            "prompt": {"type": "string", "description": "Reserved while generation is disabled."},
            "image_path": {
                "type": "string",
                "description": "Reserved while local-file upload is disabled.",
            },
            "job_id": {"type": "string", "description": "Reserved while polling is disabled."},
            "model_url": {
                "type": "string",
                "description": "Reserved while caller-selected URL import is disabled.",
            },
        },
        "required": ["action"],
    },
)
@validated_handler(actions=[Action.value for Action in Hyper3DAction])
@ensure_main_thread
def integration_hyper3d(
    action: Optional[str] = None, **params: Any
) -> Dict[str, Any]:
    """Return configuration status or deny quarantined external actions."""
    del params

    if not action:
        return {"error": "Missing required parameter: 'action'", "code": "MISSING_ACTION"}
    if action == Hyper3DAction.STATUS.value:
        return _get_status()
    if action in ExternalActions:
        return {
            "error": ExternalCapabilityMessage,
            "code": "EXTERNAL_CAPABILITY_DISABLED",
            "action": action,
            "retry_safe": False,
        }
    return {"error": f"Unknown action: {action}", "code": "UNKNOWN_ACTION"}


def _get_status() -> Dict[str, Any]:
    """Report saved configuration without reading any Scene-stored API key."""
    try:
        ConfiguredEnabled = bool(
            getattr(bpy.context.scene, "blendermcp_use_hyper3d", False)
        )
        Mode = getattr(bpy.context.scene, "blendermcp_hyper3d_mode", "RODIN")
        return {
            "success": True,
            "enabled": ConfiguredEnabled,
            "configured_enabled": ConfiguredEnabled,
            "authenticated": False,
            "operational": False,
            "external_actions_available": False,
            "mode": Mode,
            "message": (
                "Hyper3D configuration is enabled, but external actions are disabled by "
                "security policy"
                if ConfiguredEnabled
                else "Hyper3D configuration is disabled; external actions are also disabled "
                "by security policy"
            ),
        }
    except (AttributeError, TypeError):
        return {"error": "Failed to get Hyper3D configuration status", "code": "STATUS_ERROR"}
