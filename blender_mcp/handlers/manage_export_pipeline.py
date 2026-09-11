"""
Production Export Pipeline Handler for Blender MCP 1.0.0

Implements:
- glTF/USD/Alembic/FBX export
- Batch multi-format export
- Export validation
- Preset-based configuration

High Mode: Export anything, anywhere, perfectly.
"""

from ..dispatcher import register_handler
from ..core.parameter_validator import validated_handler
from ..core.enums import ExportPipelineAction
from ..core.response_builder import ResponseBuilder
from ..core.security import Capability
from ..core.validation_utils import ValidationUtils
from ..core.export_pipeline import (
    GLTFExporter,
    USDExporter,
    AlembicExporter,
    FBXExporter,
    BatchExporter,
    ExportValidator,
    AuthorizeExternalImageInputs,
)
from ..core.versioning import BlenderCompatibility
from ..core.thread_safety import ensure_main_thread
from ..utils.error_handler import mcp_tool_handler
from ..utils.path_validator import PathValidator

import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import bpy

    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False
    bpy = None


ExportPipelineCapabilities = {
    Action.value: [Capability.MUTATE.value] for Action in ExportPipelineAction
}
for ExportActionName in (
    ExportPipelineAction.EXPORT_GLTF.value,
    ExportPipelineAction.EXPORT_GLTF_DRACO.value,
    ExportPipelineAction.EXPORT_USD.value,
    ExportPipelineAction.EXPORT_ALEMBIC.value,
    ExportPipelineAction.EXPORT_FBX.value,
    ExportPipelineAction.EXPORT_OBJ.value,
    ExportPipelineAction.EXPORT_ALL_FORMATS.value,
    ExportPipelineAction.EXPORT_GAMEDEV_READY.value,
):
    ExportPipelineCapabilities[ExportActionName] = [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
    ]
ExportPipelineCapabilities[ExportPipelineAction.CHECK_EXPORT_PATH.value] = [
    Capability.READ.value,
    Capability.FILESYSTEM_WRITE.value,
]
ExportPipelineCapabilities[ExportPipelineAction.VALIDATE_GLTF.value] = [
    Capability.READ.value,
    Capability.FILESYSTEM_READ.value,
]
ExportPipelineCapabilities[ExportPipelineAction.VALIDATE_FOR_EXPORT.value] = [
    Capability.READ.value
]


def _ConcreteExportPath(Action: str | None, FilePath: str) -> str:
    ExtensionsByAction = {
        ExportPipelineAction.EXPORT_GLTF.value: ((".gltf", ".glb"), ".glb"),
        ExportPipelineAction.EXPORT_GLTF_DRACO.value: ((".gltf", ".glb"), ".glb"),
        ExportPipelineAction.EXPORT_USD.value: ((".usd", ".usda", ".usdc", ".usdz"), ".usd"),
        ExportPipelineAction.EXPORT_ALEMBIC.value: ((".abc",), ".abc"),
        ExportPipelineAction.EXPORT_FBX.value: ((".fbx",), ".fbx"),
        ExportPipelineAction.EXPORT_OBJ.value: ((".obj",), ".obj"),
    }
    Rule = ExtensionsByAction.get(Action)
    if Rule is None or FilePath.lower().endswith(Rule[0]):
        return FilePath
    return FilePath + Rule[1]


@register_handler(
    "manage_export_pipeline",
    capabilities=ExportPipelineCapabilities,
    schema={
        "type": "object",
        "title": "Export Pipeline Manager",
        "description": (
            "STANDARD — Multi-format export pipeline manager.\n"
            "ACTIONS: EXPORT_FBX, EXPORT_OBJ, EXPORT_GLTF, EXPORT_GLTF_DRACO, EXPORT_USD, "
            "EXPORT_ALEMBIC, EXPORT_ALL_FORMATS, EXPORT_GAMEDEV_READY, "
            "VALIDATE_FOR_EXPORT, VALIDATE_GLTF, CHECK_EXPORT_PATH\n\n"
            "NOTE: Use selected_only=True to export only selected objects. "
            "Always set filepath with correct extension (.glb, .fbx, .usd). "
            "GLTF/GLB preferred for web/game engines. FBX preferred for Unity/Unreal.\n"
            "EXPORT_GAMEDEV_READY: Runs pre-export validation + auto-fix before export."
        ),
        "properties": {
            "action": ValidationUtils.generate_enum_schema(
                ExportPipelineAction, "Export pipeline action"
            ),
            # Objects to export
            "object_names": {"type": "array", "items": {"type": "string"}},
            "object_indices": {"type": "array", "items": {"type": "integer"}},
            "selected_only": {"type": "boolean", "default": False},
            # Export path
            "filepath": {"type": "string"},
            "base_path": {"type": "string"},
            # Format-specific
            "preset": {"type": "string", "default": "game_engine"},
            "presets": {"type": "object"},
            "formats": {
                "type": "array",
                "items": {"type": "string", "enum": ["GLB", "USD", "FBX", "OBJ"]},
                "default": ["GLB", "FBX", "USD"],
            },
            # glTF/Draco settings
            "quantization_bits": {"type": "integer", "default": 14},
            "compression_level": {"type": "integer", "default": 7},
            "custom_settings": {"type": "object"},
            # Alembic settings
            "frame_start": {"type": "integer"},
            "frame_end": {"type": "integer"},
            "frame_step": {"type": "integer", "default": 1},
            "export_uvs": {"type": "boolean", "default": True},
            "export_normals": {"type": "boolean", "default": True},
            "flatten_hierarchy": {"type": "boolean", "default": False},
            # Validation
            "export_format": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
            "force_export": {
                "type": "boolean",
                "default": False,
                "description": "Deprecated security bypass. Requests with true are denied.",
            },
        },
        "required": ["action"],
    },
)
@validated_handler(actions=[a.value for a in ExportPipelineAction])
@ensure_main_thread
@mcp_tool_handler
def manage_export_pipeline(action: str | None = None, **params: Any) -> dict[str, Any]:
    """
    Production export pipeline with multiple format support.
    """
    if not BPY_AVAILABLE:
        return ResponseBuilder.error(
            handler="manage_export_pipeline",
            action=action,
            error_code="NO_CONTEXT",
            message="Blender context not available",
        )

    if params.get("force_export", False):
        return ResponseBuilder.error(
            handler="manage_export_pipeline",
            action=action,
            error_code="FORCE_EXPORT_DENIED",
            message="force_export cannot bypass local filesystem or resource policy",
        )

    # Get objects to export
    objects = []

    if "object_names" in params:
        for name in params["object_names"]:
            obj = bpy.data.objects.get(name)
            if obj:
                objects.append(obj)
    elif "object_indices" in params:
        for idx in params["object_indices"]:
            obj = BlenderCompatibility.get_object_by_index(idx)
            if obj:
                objects.append(obj)
    elif params.get("selected_only"):
        objects = list(bpy.context.selected_objects)
    else:
        # bpy.context.scene can be restricted / empty in timer callbacks.
        # Use a fallback chain: context scene → first data scene → all data objects.
        try:
            ctx_scene = bpy.context.scene
            objects = list(ctx_scene.objects) if ctx_scene else []
        except Exception:
            objects = []
        if not objects:
            try:
                objects = list(bpy.data.scenes[0].objects)
            except Exception:
                objects = []
        if not objects:
            objects = list(bpy.data.objects)

    if not objects and action not in [
        ExportPipelineAction.VALIDATE_GLTF.value,
        ExportPipelineAction.CHECK_EXPORT_PATH.value,
    ]:
        return ResponseBuilder.error(
            handler="manage_export_pipeline",
            action=action,
            error_code="OBJECT_INVALID",
            message="No objects to export",
        )

    scene = bpy.context.scene

    GlbActions = {
        ExportPipelineAction.EXPORT_GLTF.value,
        ExportPipelineAction.EXPORT_GLTF_DRACO.value,
    }
    RequestedFormats = [
        str(Format).upper()
        for Format in params.get("formats", ["GLB", "FBX", "USD"])
    ]
    if action in GlbActions or (
        action
        in {
            ExportPipelineAction.EXPORT_ALL_FORMATS.value,
            ExportPipelineAction.EXPORT_GAMEDEV_READY.value,
        }
        and "GLB" in RequestedFormats
    ):
        try:
            AuthorizeExternalImageInputs()
        except Exception as Error:
            return ResponseBuilder.error(
                handler="manage_export_pipeline",
                action=action,
                error_code="GLB_INPUT_DENIED",
                message=str(Error),
            )

    # Zırhlı İhracat: Unified Pre-Flight Validation
    export_actions = {
        ExportPipelineAction.EXPORT_GLTF.value,
        ExportPipelineAction.EXPORT_GLTF_DRACO.value,
        ExportPipelineAction.EXPORT_USD.value,
        ExportPipelineAction.EXPORT_ALEMBIC.value,
        ExportPipelineAction.EXPORT_FBX.value,
        ExportPipelineAction.EXPORT_OBJ.value,
        ExportPipelineAction.EXPORT_ALL_FORMATS.value,
        ExportPipelineAction.EXPORT_GAMEDEV_READY.value,
    }

    if action in export_actions:
        filepath = params.get("filepath")
        # For batch operations or if no filepath, we might skip path check here
        # but we ALWAYS check geometry complexity.
        try:
            # Geometry limits check
            ExportValidator.validate_for_export(objects, export_format=str(action))
            # Path injection check (if single file variant)
            if filepath:
                filepath = _ConcreteExportPath(action, filepath)
                PathCheck = ExportValidator.check_export_path(
                    filepath,
                    overwrite=params.get("overwrite", False),
                )
                if not PathCheck["valid"]:
                    return ResponseBuilder.error(
                        handler="manage_export_pipeline",
                        action=action,
                        error_code="INVALID_PATH",
                        message="Export path failed validation",
                        details={"issues": PathCheck["issues"]},
                    )

        except ValueError as ve:
            return ResponseBuilder.error(
                handler="manage_export_pipeline",
                action=action,
                error_code="EXPORT_ARMOR_BLOCKED",
                message=str(ve),
            )

    try:
        # glTF Export
        if action == ExportPipelineAction.EXPORT_GLTF.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )
            filepath = _ConcreteExportPath(action, filepath)

            try:
                filepath = PathValidator.validate_and_prepare(
                    filepath,
                    {".glb"},
                    overwrite=params.get("overwrite", False),
                )
            except Exception as e:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="INVALID_PATH",
                    message=str(e),
                )

            export_result = GLTFExporter.export(
                scene,
                objects,
                filepath,
                preset=params.get("preset", "game_engine"),
                custom_settings=params.get("custom_settings"),
            )
            if isinstance(export_result, dict) and export_result.get("success"):
                return ResponseBuilder.success(
                    handler="manage_export_pipeline",
                    action=action,
                    data=export_result,
                )
            return export_result  # type: ignore[no-any-return]

        elif action == ExportPipelineAction.EXPORT_GLTF_DRACO.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )
            filepath = _ConcreteExportPath(action, filepath)

            try:
                filepath = PathValidator.validate_and_prepare(
                    filepath,
                    {".glb"},
                    overwrite=params.get("overwrite", False),
                )
            except Exception as e:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="INVALID_PATH",
                    message=str(e),
                )

            # Zırh: Explicit Type Casting for C-Level safety
            try:
                q_bits = int(params.get("quantization_bits", 14))
                q_bits = max(0, min(30, q_bits))
            except (TypeError, ValueError):
                q_bits = 14

            try:
                c_level = int(params.get("compression_level", 7))
                c_level = max(0, min(10, c_level))
            except (TypeError, ValueError):
                c_level = 7

            return GLTFExporter.export_with_draco(
                scene,
                objects,
                filepath,
                quantization_bits=q_bits,
                compression_level=c_level,
            )

        # USD Export
        elif action == ExportPipelineAction.EXPORT_USD.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )
            filepath = _ConcreteExportPath(action, filepath)

            try:
                filepath = PathValidator.validate_and_prepare(
                    filepath,
                    {".usd", ".usda", ".usdc", ".usdz"},
                    overwrite=params.get("overwrite", False),
                )
            except Exception as e:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="INVALID_PATH",
                    message=str(e),
                )

            return USDExporter.export(  # type: ignore[no-any-return]
                scene,
                objects,
                filepath,
                preset=params.get("preset", "omniverse"),
                custom_settings=params.get("custom_settings"),
            )

        # Alembic Export
        elif action == ExportPipelineAction.EXPORT_ALEMBIC.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )
            filepath = _ConcreteExportPath(action, filepath)
            try:
                filepath = PathValidator.validate_and_prepare(
                    filepath,
                    {".abc"},
                    overwrite=params.get("overwrite", False),
                )
            except Exception as e:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="INVALID_PATH",
                    message=str(e),
                )

            return AlembicExporter.export_animation(  # type: ignore[no-any-return]
                scene,
                objects,
                filepath,
                frame_start=params.get("frame_start"),
                frame_end=params.get("frame_end"),
                frame_step=params.get("frame_step", 1),
                export_uvs=params.get("export_uvs", True),
                export_normals=params.get("export_normals", True),
                flatten_hierarchy=params.get("flatten_hierarchy", False),
            )

        # FBX Export
        elif action == ExportPipelineAction.EXPORT_FBX.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )
            filepath = _ConcreteExportPath(action, filepath)

            try:
                filepath = PathValidator.validate_and_prepare(
                    filepath,
                    {".fbx"},
                    overwrite=params.get("overwrite", False),
                )
            except Exception as e:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="INVALID_PATH",
                    message=str(e),
                )

            return FBXExporter.export(  # type: ignore[no-any-return]
                objects,
                filepath,
                preset=params.get("preset", "unity"),
                custom_settings=params.get("custom_settings"),
            )

        # OBJ Export (simplified)
        elif action == ExportPipelineAction.EXPORT_OBJ.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )
            filepath = _ConcreteExportPath(action, filepath)

            try:
                filepath = PathValidator.validate_and_prepare(
                    filepath,
                    {".obj"},
                    overwrite=params.get("overwrite", False),
                )
            except Exception as e:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="INVALID_PATH",
                    message=str(e),
                )

            Result = BatchExporter._export_obj(objects, filepath)
            Result["objects_exported"] = len(objects)
            return Result

        # Game Dev Ready — multi-format batch export to a directory
        elif action == ExportPipelineAction.EXPORT_GAMEDEV_READY.value:
            base_path = params.get("base_path")
            if not base_path:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'base_path' (target directory)",
                )

            mesh_objects = [o for o in objects if getattr(o, "type", None) == "MESH"]
            if not mesh_objects:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="NO_MESH_OBJECTS",
                    message="No mesh objects found. EXPORT_GAMEDEV_READY requires at least one MESH object.",
                )

            formats = [f.upper() for f in params.get("formats", ["GLB", "FBX", "USD"])]
            preset = params.get("preset", "game_engine")
            results: dict = {}
            errors: dict = {}

            for fmt in formats:
                try:
                    if fmt == "GLB":
                        ext = ".glb"
                        fp = PathValidator.validate_and_prepare(
                            os.path.join(base_path, f"export{ext}"),
                            {".glb"},
                            overwrite=params.get("overwrite", False),
                        )
                        r = GLTFExporter.export(scene, mesh_objects, fp, preset=preset)
                    elif fmt == "FBX":
                        fp = PathValidator.validate_and_prepare(
                            os.path.join(base_path, "export.fbx"),
                            {".fbx"},
                            overwrite=params.get("overwrite", False),
                        )
                        r = FBXExporter.export(mesh_objects, fp, preset="unity")
                    elif fmt in ("USD", "USDA", "USDC", "USDZ"):
                        ext = f".{fmt.lower()}"
                        fp = PathValidator.validate_and_prepare(
                            os.path.join(base_path, f"export{ext}"),
                            {".usd", ".usda", ".usdc", ".usdz"},
                            overwrite=params.get("overwrite", False),
                        )
                        r = USDExporter.export(scene, mesh_objects, fp)
                    else:
                        errors[fmt] = f"Unsupported format: {fmt}"
                        continue
                    results[fmt] = r
                except Exception as e:
                    errors[fmt] = str(e)

            success = bool(results) and not errors
            return (
                ResponseBuilder.success(
                    handler="manage_export_pipeline",
                    action=action,
                    data={
                        "base_path": base_path,
                        "formats_requested": formats,
                        "results": results,
                        "errors": errors if errors else None,
                        "objects_exported": len(mesh_objects),
                    },
                )
                if success
                else ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="PARTIAL_OR_FULL_FAILURE",
                    message=f"Export completed with errors: {errors}"
                    if errors
                    else "Export failed",
                    details={"results": results, "errors": errors},
                )
            )

        # Batch Export
        elif action == ExportPipelineAction.EXPORT_ALL_FORMATS.value:
            base_path = params.get("base_path")
            if not base_path:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'base_path'",
                )

            return BatchExporter.export_all_formats(  # type: ignore[no-any-return]
                scene,
                objects,
                base_path,
                formats=params.get("formats", ["GLB", "FBX", "USD"]),
                presets=params.get("presets", {}),
                overwrite=params.get("overwrite", False),
            )

        # Validation
        elif action == ExportPipelineAction.VALIDATE_FOR_EXPORT.value:
            export_format = params.get("export_format", "GLB")
            return ExportValidator.validate_for_export(objects, export_format)

        elif action == ExportPipelineAction.CHECK_EXPORT_PATH.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )

            return ExportValidator.check_export_path(
                filepath, overwrite=params.get("overwrite", False)
            )

        elif action == ExportPipelineAction.VALIDATE_GLTF.value:
            filepath = params.get("filepath")
            if not filepath:
                return ResponseBuilder.error(
                    handler="manage_export_pipeline",
                    action=action,
                    error_code="MISSING_PARAMETER",
                    message="Missing required parameter: 'filepath'",
                )

            return GLTFExporter.validate_gltf(filepath)

        else:
            return ResponseBuilder.error(
                handler="manage_export_pipeline",
                action=action,
                error_code="INVALID_PARAMETER_VALUE",
                message=f"Unknown export action: {action}",
            )

    except Exception as e:
        return ResponseBuilder.error(
            handler="manage_export_pipeline",
            action=action,
            error_code="EXPORT_ERROR",
            message=f"Export failed: {str(e)}",
        )
