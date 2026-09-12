"""Security boundary for the optional Poly Haven integration.

External actions remain quarantined until purpose-scoped network, filesystem,
download, content-validation, and provider-job controls exist. STATUS only
reports saved configuration and never performs external I/O.
"""

from typing import Any, Dict, Optional

import bpy

from ..core.enums import PolyHavenAction
from ..core.parameter_validator import validated_handler
from ..core.security import Capability
from ..core.thread_safety import ensure_main_thread
from ..core.validation_utils import ValidationUtils
from ..dispatcher import register_handler


# ``importlib.reload`` reuses a module dictionary. Remove every retired sink so
# hot reload cannot preserve callable network, temporary-file, or import paths.
for LegacyUnsafeName in (
    "os",
    "tempfile",
    "requests",
    "ContextManagerV3",
    "safe_ops",
    "_search",
    "_import_hdri",
    "_import_model",
    "_import_material",
):
    globals().pop(LegacyUnsafeName, None)
del LegacyUnsafeName


PolyHavenCapabilities = {
    PolyHavenAction.STATUS.value: [Capability.READ.value],
    PolyHavenAction.SEARCH.value: [Capability.READ.value, Capability.NETWORK.value],
    PolyHavenAction.IMPORT_HDRI.value: [
        Capability.MUTATE.value,
        Capability.NETWORK.value,
        Capability.FILESYSTEM_READ.value,
        Capability.FILESYSTEM_WRITE.value,
    ],
    PolyHavenAction.IMPORT_MODEL.value: [
        Capability.MUTATE.value,
        Capability.NETWORK.value,
        Capability.FILESYSTEM_READ.value,
        Capability.FILESYSTEM_WRITE.value,
    ],
    PolyHavenAction.IMPORT_MATERIAL.value: [
        Capability.MUTATE.value,
        Capability.NETWORK.value,
        Capability.FILESYSTEM_READ.value,
        Capability.FILESYSTEM_WRITE.value,
    ],
}
ExternalActions = frozenset(
    Action.value for Action in PolyHavenAction if Action is not PolyHavenAction.STATUS
)
ExternalCapabilityMessage = (
    "Poly Haven external actions are disabled until purpose-scoped network, filesystem, "
    "download, content-validation, and provider job controls are available"
)


@register_handler(
    "integration_polyhaven",
    actions=[Action.value for Action in PolyHavenAction],
    capabilities=PolyHavenCapabilities,
    schema={
        "type": "object",
        "title": "Poly Haven Integration",
        "description": (
            "Inspect Poly Haven configuration. Search, download, and import actions are "
            "quarantined by the security policy."
        ),
        "properties": {
            "action": ValidationUtils.generate_enum_schema(PolyHavenAction, "Operation to perform"),
            "query": {"type": "string", "description": "Reserved while search is disabled."},
            "asset_type": {
                "type": "string",
                "enum": ["hdris", "models", "textures"],
                "description": "Reserved while search is disabled.",
            },
            "asset_id": {"type": "string", "description": "Reserved while import is disabled."},
            "resolution": {
                "type": "string",
                "enum": ["1k", "2k", "4k", "8k"],
                "default": "4k",
                "description": "Reserved while import is disabled.",
            },
        },
        "required": ["action"],
    },
)
@validated_handler(actions=[Action.value for Action in PolyHavenAction])
@ensure_main_thread
def integration_polyhaven(action: Optional[str] = None, **params: Any) -> Dict[str, Any]:
    """Return configuration status or deny quarantined external actions."""
    del params

    if not action:
        return {"error": "Missing required parameter: 'action'", "code": "MISSING_ACTION"}
    if action == PolyHavenAction.STATUS.value:
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
    """Return saved configuration without implying operational availability."""
    try:
        ConfiguredEnabled = bool(getattr(bpy.context.scene, "blendermcp_use_polyhaven", True))
        return {
            "success": True,
            "enabled": ConfiguredEnabled,
            "configured_enabled": ConfiguredEnabled,
            "operational": False,
            "external_actions_available": False,
            "message": (
                "Poly Haven configuration is enabled, but external actions are disabled by "
                "security policy"
                if ConfiguredEnabled
                else "Poly Haven configuration is disabled; external actions are also disabled "
                "by security policy"
            ),
        }
    except (AttributeError, TypeError):
        return {"error": "Failed to get Poly Haven configuration status", "code": "STATUS_ERROR"}
