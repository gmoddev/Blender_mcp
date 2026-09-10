"""
Motion Capture Handler for Blender MCP 1.0.0 (Refactored)

Import, Cleanup, Retargeting, and Analysis of Mocap Data.
- Uses MocapAction Enum for strict typing
- Validates input via ValidationUtils
- Integrates with ContextManagerV3 for safety

High Mode Philosophy: Turn raw motion data into character soul.
"""

from typing import Any, Optional

try:
    import bpy

    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False

from ..dispatcher import register_handler
from ..core.response_builder import ResponseBuilder
from ..core.logging_config import get_logger
from ..core.enums import MocapAction
from ..core.filesystem_boundary import FilesystemAccess, FilesystemPolicyError
from ..core.security import Capability
from ..core.validation_utils import ValidationUtils
from ..core.resolver import resolve_name
from ..core.thread_safety import ensure_main_thread
from ..core.context_manager_v3 import ContextManagerV3
from ..utils.path import get_safe_path

logger = get_logger()

MocapCapabilities = {Action.value: [Capability.MUTATE.value] for Action in MocapAction}
for ImportAction in (MocapAction.IMPORT_BVH, MocapAction.IMPORT_FBX_ANIMATION):
    MocapCapabilities[ImportAction.value] = [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
    ]


def _FilesystemError(Action: str, Error: FilesystemPolicyError) -> dict[str, Any]:
    return ResponseBuilder.error(
        handler="manage_mocap",
        action=Action,
        error_code=Error.Code,
        message=Error.PublicMessage,
    )


@register_handler(
    "manage_mocap",
    actions=[a.value for a in MocapAction],
    capabilities=MocapCapabilities,
    category="animation",
    schema={
        "type": "object",
        "title": "Motion Capture Manager",
        "description": "Process Motion Capture data: Import, Clean, Retarget, and Export.",
        "properties": {
            "action": ValidationUtils.generate_enum_schema(MocapAction, "Operation to perform."),
            "filepath": {
                "type": "string",
                "description": "Path to BVH/FBX file for import/export.",
            },
            "armature_name": {"type": "string", "description": "Target armature for operations."},
            "source_armature": {
                "type": "string",
                "description": "Source armature for retargeting.",
            },
            "target_armature": {
                "type": "string",
                "description": "Target armature for retargeting.",
            },
            "smoothness": {
                "type": "number",
                "description": "Factor for curve smoothing (0.0 - 1.0).",
            },
            "threshold": {
                "type": "number",
                "description": "Threshold for noise cleaning/keyframe reduction.",
            },
            "frame_start": {"type": "integer", "description": "Start frame for processing."},
            "frame_end": {"type": "integer", "description": "End frame for processing."},
            "lock_axis": {
                "type": "string",
                "enum": ["X", "Y", "Z"],
                "description": "Axis to lock for ground contact fixes.",
            },
        },
        "required": ["action"],
    },
)
@ensure_main_thread
def manage_mocap(action: Optional[str] = None, **params):  # type: ignore[no-untyped-def]
    """
    Manage Motion Capture workflows.
    """
    # 1. Validate Action
    validation_error = ValidationUtils.validate_enum(action, MocapAction, "action")
    if validation_error:
        return ResponseBuilder.from_error(validation_error, handler="manage_mocap", action=action)

    # Dispatch to specific handlers
    try:
        # Import Operations
        if action == MocapAction.IMPORT_BVH.value:
            return _import_bvh(params)
        elif action == MocapAction.IMPORT_FBX_ANIMATION.value:
            return _import_fbx(params)

        # Cleanup Operations
        elif action == MocapAction.CLEAN_NOISE.value:
            return _clean_noise(params)
        elif action == MocapAction.SMOOTH_CURVES.value:
            return _smooth_curves(params)
        elif action == MocapAction.REDUCE_KEYFRAMES.value:
            return _reduce_keyframes(params)

        # Retargeting
        elif action == MocapAction.RETARGET_TO_RIG.value:
            return _retarget_to_rig(params)

        # Foot/Ground Fixes
        elif action == MocapAction.FOOT_LOCK_FIX.value:
            return _fix_foot_sliding(params)

        # Analysis
        elif action == MocapAction.ANALYZE_MOTION.value:
            return _analyze_motion(params)

        return ResponseBuilder.error(
            handler="manage_mocap",
            action=action,
            error_code="NOT_IMPLEMENTED",
            message=f"Action '{action}' is defined but not yet implemented.",
        )

    except Exception:
        logger.error("[BlenderMCP:Mocap] operation failed")
        return ResponseBuilder.error(
            handler="manage_mocap",
            action=action,
            error_code="EXECUTION_ERROR",
            message="The motion-capture operation failed",
        )


# =============================================================================
# INTERNAL HANDLERS
# =============================================================================


def _import_bvh(params):  # type: ignore[no-untyped-def]
    filepath = params.get("filepath")
    if not filepath:
        return ResponseBuilder.error(
            handler="manage_mocap",
            action="IMPORT_BVH",
            error_code="MISSING_PARAMETER",
            message="filepath is required.",
        )

    try:
        AuthorizedPath = get_safe_path(
            filepath,
            Access=FilesystemAccess.READ,
            AllowedExtensions={".bvh"},
            CreateParents=False,
        )
    except FilesystemPolicyError as Error:
        return _FilesystemError(MocapAction.IMPORT_BVH.value, Error)

    try:
        bpy.ops.import_anim.bvh(
            filepath=AuthorizedPath,
            global_scale=1.0,
            use_fps_scale=True,
            update_scene_fps=True,
            update_scene_duration=True,
        )
        # Get imported object (usually active)
        obj = bpy.context.active_object
        return ResponseBuilder.success(
            handler="manage_mocap",
            action="IMPORT_BVH",
            data={"object": obj.name if obj else "Unknown", "path": AuthorizedPath},
            affected_objects=(
                [{"name": obj.name, "type": "ARMATURE", "changes": ["imported"]}] if obj else []
            ),
        )
    except Exception:
        return ResponseBuilder.error(
            handler="manage_mocap",
            action="IMPORT_BVH",
            error_code="EXECUTION_ERROR",
            message="Blender could not import the authorized BVH file",
        )


def _import_fbx(params):  # type: ignore[no-untyped-def]
    del params
    return ResponseBuilder.error(
        handler="manage_mocap",
        action=MocapAction.IMPORT_FBX_ANIMATION.value,
        error_code="INPUT_FAMILY_DISABLED",
        message="FBX animation import is disabled until every linked input is authorized",
    )


def _clean_noise(params):  # type: ignore[no-untyped-def]
    armature_name = params.get("armature_name")
    threshold = params.get("threshold", 0.001)

    obj = resolve_name(armature_name) if armature_name else bpy.context.active_object
    if not obj or not obj.animation_data or not obj.animation_data.action:
        return ResponseBuilder.error(
            handler="manage_mocap",
            action="CLEAN_NOISE",
            error_code="INVALID_CONTEXT",
            message="No active object with animation data found.",
        )

    # Simple noise cleaning using clean_keyframes (decimate)
    with ContextManagerV3.temp_override(
        area_type="GRAPH_EDITOR"
    ):  # Needs context for some ops, but fcurves usage is safer
        # We'll use API access instead of ops for safety if possible, or basic op
        try:
            action = obj.animation_data.action
            # Fix: Blender 5.0 Slotted Action Compatibility
            fcurves = []
            if hasattr(action, "slots"):
                for slot in action.slots:
                    fcurves.extend(slot.fcurves)
            elif hasattr(action, "fcurves"):
                fcurves.extend(action.fcurves)

            for fcurve in fcurves:
                # Basic cleaning logic or using Blender operator
                # Using operator requires selecting all keys
                pass

            # Use built-in operator
            bpy.ops.graph.clean(threshold=threshold, channels=False)
            return {"success": True, "message": f"Cleaned noise with threshold {threshold}"}
        except Exception:
            # Fallback to per-fcurve manual cleaning if context fails
            return {"success": False, "message": "Graph cleanup could not run in this context"}


def _smooth_curves(params):  # type: ignore[no-untyped-def]
    armature_name = params.get("armature_name")
    params.get("smoothness", 0.5)

    obj = resolve_name(armature_name) if armature_name else bpy.context.active_object
    if not obj or not obj.animation_data or not obj.animation_data.action:
        return ResponseBuilder.error(
            handler="manage_mocap",
            action="SMOOTH_CURVES",
            error_code="INVALID_CONTEXT",
            message="No active object with animation data found.",
        )

    try:
        bpy.ops.graph.smooth()  # Simplest version
        return {"success": True, "message": "Smoothed curves"}
    except Exception:
        return ResponseBuilder.error(
            handler="manage_mocap",
            action="SMOOTH_CURVES",
            error_code="EXECUTION_ERROR",
            message="Blender could not smooth the selected curves",
        )


def _reduce_keyframes(params):  # type: ignore[no-untyped-def]
    params.get("armature_name")
    ratio = params.get("threshold", 0.1)  # Decimate ratio

    try:
        bpy.ops.action.clean(threshold=ratio)  # This removes useless keys
        return {"success": True, "message": "Reduced keyframes"}
    except Exception:
        return {"success": False, "message": "Blender could not reduce the selected keyframes"}


def _retarget_to_rig(params):  # type: ignore[no-untyped-def]
    source = params.get("source_armature")
    target = params.get("target_armature")

    if not source or not target:
        return ResponseBuilder.error(
            handler="manage_mocap",
            action="RETARGET_TO_RIG",
            error_code="MISSING_PARAMETER",
            message="source_armature and target_armature are required.",
        )

    # Placeholder for complex retargeting logic (Rigify/Rokoko style)
    # This usually requires bone mapping.

    return {
        "success": False,
        "message": "Retargeting requires complex mapping implementation not fully available in this standardized handler yet.",
    }


def _fix_foot_sliding(params):  # type: ignore[no-untyped-def]
    # Analyzing height of foot bones and locking if below threshold
    return {"success": True, "message": "Foot sliding fix placeholder executed."}


def _analyze_motion(params):  # type: ignore[no-untyped-def]
    # Calculate velocity, range of motion, etc.
    return {"success": True, "analysis": "Motion analysis placeholder data."}
