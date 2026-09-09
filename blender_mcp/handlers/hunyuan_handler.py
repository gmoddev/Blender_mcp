"""Security boundary for the optional Tencent Hunyuan3D integration.

External Hunyuan actions are intentionally quarantined until the project has
purpose-scoped network, credential, filesystem, download, archive, and provider
job lifecycle controls. STATUS remains available so callers can inspect the
saved configuration without causing external I/O.
"""

from typing import Any, Dict

import bpy

from ..core.enums import HunyuanAction
from ..core.parameter_validator import validated_handler
from ..core.security import Capability
from ..core.thread_safety import ensure_main_thread
from ..core.validation_utils import ValidationUtils
from ..dispatcher import register_handler


# ``importlib.reload`` reuses a module dictionary. Purge names from the retired
# implementation so add-on hot reload cannot leave its unsafe helpers callable.
for LegacyUnsafeName in (
    "base64",
    "datetime",
    "hashlib",
    "hmac",
    "json",
    "os",
    "osp",
    "tempfile",
    "time",
    "zipfile",
    "requests",
    "ContextManagerV3",
    "safe_ops",
    "_create_job",
    "_poll_job",
    "_import_asset",
    "_create_job_official",
    "_create_job_local",
    "get_tencent_cloud_sign_headers",
):
    globals().pop(LegacyUnsafeName, None)
del LegacyUnsafeName


HunyuanCapabilities = {
    HunyuanAction.STATUS.value: [Capability.READ.value],
    HunyuanAction.GENERATE.value: [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
        Capability.FILESYSTEM_WRITE.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
    ],
    HunyuanAction.CHECK_JOB.value: [
        Capability.READ.value,
        Capability.NETWORK.value,
        Capability.CREDENTIAL_ACCESS.value,
    ],
    HunyuanAction.IMPORT.value: [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
        Capability.NETWORK.value,
        Capability.FILESYSTEM_WRITE.value,
    ],
}
ExternalActions = frozenset(
    {
        HunyuanAction.GENERATE.value,
        HunyuanAction.CHECK_JOB.value,
        HunyuanAction.IMPORT.value,
    }
)
ExternalCapabilityMessage = (
    "Hunyuan external actions are disabled until purpose-scoped network, credential, "
    "filesystem, download, archive, and provider job lifecycle controls are available"
)


@register_handler(
    "integration_hunyuan",
    capabilities=HunyuanCapabilities,
    schema={
        "type": "object",
        "title": "Hunyuan3D Integration",
        "description": (
            "Inspect Tencent Hunyuan3D configuration. External generation, polling, and "
            "import actions are quarantined by the security policy."
        ),
        "properties": {
            "action": ValidationUtils.generate_enum_schema(HunyuanAction, "Operation to perform."),
            "prompt": {
                "type": "string",
                "description": "Reserved for GENERATE; external actions are currently disabled.",
            },
            "image_path": {
                "type": "string",
                "description": (
                    "Reserved for GENERATE; local paths and caller-selected URLs are not accepted."
                ),
            },
            "job_id": {
                "type": "string",
                "description": "Reserved for CHECK_JOB; external actions are currently disabled.",
            },
            "zip_url": {
                "type": "string",
                "description": (
                    "Reserved for IMPORT; caller-selected download URLs are not accepted."
                ),
            },
        },
        "required": ["action"],
    },
)
@validated_handler(actions=[Action.value for Action in HunyuanAction])
@ensure_main_thread
def integration_hunyuan(action=None, **params):  # type: ignore[no-untyped-def]
    """Return configuration status or fail closed for quarantined external actions."""
    del params

    if not action:
        return {"error": "Missing required parameter: 'action'", "code": "MISSING_ACTION"}

    if action == HunyuanAction.STATUS.value:
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
    """Return configuration state without implying that external actions can execute."""
    try:
        ConfiguredEnabled = bool(
            getattr(bpy.context.scene, "blendermcp_use_hunyuan3d", False)
        )
        Mode = getattr(bpy.context.scene, "blendermcp_hunyuan3d_mode", "OFFICIAL_API")
        return {
            "success": True,
            "enabled": ConfiguredEnabled,
            "configured_enabled": ConfiguredEnabled,
            "operational": False,
            "external_actions_available": False,
            "mode": Mode,
            "message": (
                "Hunyuan configuration is enabled, but external actions are disabled by "
                "security policy"
                if ConfiguredEnabled
                else "Hunyuan configuration is disabled; external actions are also disabled "
                "by security policy"
            ),
        }
    except (AttributeError, TypeError) as Error:
        return {
            "error": f"Failed to get Hunyuan configuration status: {Error}",
            "code": "STATUS_ERROR",
        }
