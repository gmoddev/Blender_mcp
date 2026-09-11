"""Security boundary for the optional Sketchfab integration."""

from typing import Any, Dict, Optional

import bpy

from ..core.enums import SketchfabAction
from ..core.parameter_validator import validated_handler
from ..core.security import Capability
from ..core.thread_safety import ensure_main_thread
from ..core.validation_utils import ValidationUtils
from ..dispatcher import register_handler


for LegacyUnsafeName in (
    "os",
    "tempfile",
    "requests",
    "ContextManagerV3",
    "safe_ops",
    "SketchfabSearchResponse",
    "SketchfabDownloadData",
    "_search",
    "_get_download_url",
    "_import_model",
):
    globals().pop(LegacyUnsafeName, None)
del LegacyUnsafeName


SketchfabCapabilities = {
    SketchfabAction.STATUS.value: [Capability.READ.value],
    SketchfabAction.SEARCH.value: [
        Capability.READ.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
    ],
    SketchfabAction.GET_DOWNLOAD_URL.value: [
        Capability.READ.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
    ],
    SketchfabAction.IMPORT.value: [
        Capability.MUTATE.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
        Capability.FILESYSTEM_READ.value,
        Capability.FILESYSTEM_WRITE.value,
    ],
}
ExternalActions = frozenset(
    Action.value for Action in SketchfabAction if Action is not SketchfabAction.STATUS
)
ExternalCapabilityMessage = (
    "Sketchfab external actions are disabled until purpose-scoped network, credential, "
    "filesystem, download, content-validation, and provider job controls are available"
)


@register_handler(
    "integration_sketchfab",
    actions=[Action.value for Action in SketchfabAction],
    capabilities=SketchfabCapabilities,
    schema={
        "type": "object",
        "title": "Sketchfab Integration",
        "description": (
            "Inspect Sketchfab configuration. Search, download-URL, and import actions are "
            "quarantined by the security policy."
        ),
        "properties": {
            "action": ValidationUtils.generate_enum_schema(
                SketchfabAction, "Operation to perform"
            ),
            "query": {"type": "string", "description": "Reserved while search is disabled."},
            "count": {"type": "integer", "default": 10},
            "uid": {"type": "string", "description": "Reserved while import is disabled."},
        },
        "required": ["action"],
    },
)
@validated_handler(actions=[Action.value for Action in SketchfabAction])
@ensure_main_thread
def integration_sketchfab(
    action: Optional[str] = None, **params: Any
) -> Dict[str, Any]:
    """Return configuration status or deny quarantined external actions."""
    del params

    if not action:
        return {"error": "Missing required parameter: 'action'", "code": "MISSING_ACTION"}
    if action == SketchfabAction.STATUS.value:
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
    """Report the saved enablement flag without reading the Scene-stored API key."""
    try:
        ConfiguredEnabled = bool(
            getattr(bpy.context.scene, "blendermcp_use_sketchfab", False)
        )
        return {
            "success": True,
            "enabled": ConfiguredEnabled,
            "configured_enabled": ConfiguredEnabled,
            "authenticated": False,
            "operational": False,
            "external_actions_available": False,
            "message": (
                "Sketchfab configuration is enabled, but external actions are disabled by "
                "security policy"
                if ConfiguredEnabled
                else "Sketchfab configuration is disabled; external actions are also disabled "
                "by security policy"
            ),
        }
    except (AttributeError, TypeError):
        return {"error": "Failed to get Sketchfab configuration status", "code": "STATUS_ERROR"}
