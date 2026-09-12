"""
Production Export Pipeline Module for Blender MCP 1.0.0

Implements:
- glTF 2.0 export with Draco compression
- USD (Universal Scene Description) export
- Alembic cache export
- FBX/OBJ legacy support
- Export validation and optimization
- Multi-format batch export

High Mode Philosophy: Export anything, anywhere, perfectly.
"""

import os
from typing import Dict, Any, List, Optional, cast
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

try:
    import bpy

    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False
    bpy: Any = None  # type: ignore[no-redef]

from .error_protocol import ErrorProtocol, create_error
from .logging_config import get_logger
from .context_manager_v3 import ContextManagerV3
from .filesystem_boundary import FilesystemAccess, GetFilesystemPolicy
from .thread_safety import SafeOperators, ensure_main_thread

logger = get_logger()


@ensure_main_thread
def AuthorizeExternalImageInputs() -> None:
    """Authorize every file-backed Blender image before an exporter can read it.

    The check deliberately over-approximates the images used by a selected-object
    export. Blender exporter reachability changes between releases; authorizing
    every external image datablock is safer than guessing which node graphs the
    active exporter will traverse.
    """
    if not BPY_AVAILABLE:
        return

    Images = getattr(getattr(bpy, "data", None), "images", ())
    for Image in Images:
        Source = str(getattr(Image, "source", "GENERATED")).upper()
        if Source in {"GENERATED", "VIEWER", "RENDER_RESULT"}:
            continue
        if Source != "FILE":
            raise ValueError(
                "GLB export is disabled for tiled, sequence, or movie image inputs "
                "until the complete input family can be authorized"
            )

        # Packed state is only sufficient for ordinary FILE images. A nonempty
        # packed_files collection does not prove every member of a complex image
        # family is internal, so those families must be rejected above.
        if getattr(Image, "packed_file", None) is not None:
            continue
        try:
            if len(getattr(Image, "packed_files", ())) > 0:
                continue
        except Exception:
            pass

        FilePath = str(getattr(Image, "filepath", "")).strip()
        if not FilePath:
            raise ValueError("GLB export found a file-backed image without a usable path")
        try:
            AbsolutePath = bpy.path.abspath(FilePath, library=getattr(Image, "library", None))
        except TypeError:
            try:
                AbsolutePath = bpy.path.abspath(FilePath)
            except Exception as Error:
                raise ValueError("GLB export could not resolve an external image path") from Error
        except Exception as Error:
            raise ValueError("GLB export could not resolve an external image path") from Error
        GetFilesystemPolicy().RequirePath(str(AbsolutePath), FilesystemAccess.READ)


def _GetUsdTextureSettings() -> Dict[str, Any]:
    """Return version-correct settings that prevent USD texture sidecar writes."""
    try:
        Properties = set(bpy.ops.wm.usd_export.get_rna_type().properties.keys())
    except Exception as Error:
        raise RuntimeError("USD texture output policy could not be verified") from Error

    Settings: Dict[str, Any] = {}
    if "export_textures_mode" in Properties:
        Settings["export_textures_mode"] = "KEEP"
    elif "export_textures" in Properties:
        Settings["export_textures"] = False
    else:
        raise RuntimeError("This Blender version has no supported USD texture output control")
    if "overwrite_textures" in Properties:
        Settings["overwrite_textures"] = False
    if "convert_world_material" in Properties:
        Settings["convert_world_material"] = False
    return Settings


def _MergeSafeExporterSettings(
    BaseSettings: Dict[str, Any],
    CustomSettings: Optional[Dict[str, Any]],
    LockedSettings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge exporter settings without allowing secondary output paths."""
    if CustomSettings is not None and not isinstance(CustomSettings, dict):
        raise ValueError("Custom exporter settings must be an object")

    LockedSettings = LockedSettings or {}
    for SettingName in CustomSettings or {}:
        if not isinstance(SettingName, str):
            raise ValueError("Custom exporter setting names must be strings")
        NormalizedName = str(SettingName).lower()
        if (
            "path" in NormalizedName
            or "directory" in NormalizedName
            or NormalizedName.endswith("_dir")
        ):
            raise ValueError("Path-bearing custom exporter settings are not allowed")
        if SettingName in LockedSettings:
            raise ValueError("Output-family custom exporter settings are not allowed")

    Settings = BaseSettings.copy()
    Settings.update(CustomSettings or {})
    Settings.update(LockedSettings)
    return Settings


class ExportFormat(Enum):
    """Supported export formats."""

    GLTF = "GLTF"
    GLB = "GLB"
    USD = "USD"
    USDA = "USDA"  # ASCII
    USDC = "USDC"  # Binary
    ALEMBIC = "ALEMBIC"
    FBX = "FBX"
    OBJ = "OBJ"
    STL = "STL"
    PLY = "PLY"
    X3D = "X3D"


@dataclass
class ExportSettings:
    """Base export settings."""

    filepath: str
    export_format: ExportFormat
    selected_only: bool = False
    visible_only: bool = False
    apply_modifiers: bool = True
    global_scale: float = 1.0
    y_up: bool = True

    # Animation
    export_animation: bool = False
    frame_start: int = 1
    frame_end: int = 250
    frame_step: int = 1


class GLTFExporter:
    """
    glTF 2.0 exporter with advanced options.
    """

    GLTF_IMAGE_FORMAT_COMPAT = {
        "PNG": "AUTO",
        "JPEG": "JPEG",
        "WEBP": "WEBP",
        "AUTO": "AUTO",
        "NONE": "NONE",
    }

    PRESETS = {
        "web": {
            "export_format": "GLB",
            "export_draco_mesh_compression_enable": True,
            "export_draco_position_quantization": 14,
            "export_draco_normal_quantization": 10,
            "export_image_format": "AUTO",
            "export_materials": "EXPORT",
        },
        "game_engine": {
            "export_format": "GLB",
            "export_draco_mesh_compression_enable": True,
            "export_draco_position_quantization": 11,
            "export_image_format": "AUTO",
            "export_materials": "EXPORT",
            "export_skins": True,
            "export_morph": True,
        },
        "archviz": {
            "export_format": "GLB",
            "export_draco_mesh_compression_enable": False,
            "export_image_format": "JPEG",
            "export_materials": "EXPORT",
            "export_cameras": True,
            "export_lights": True,
        },
    }

    @classmethod
    def _normalize_image_format(cls, format_value: str) -> str:
        """
        Normalize image format for Blender 5.0+ glTF exporter compatibility.
        Blender 5.0+ removed 'PNG' as a valid export_image_format enum value;
        'AUTO' is the correct replacement (auto-selects PNG/JPEG as needed).
        """
        normalized = format_value.upper()
        return cls.GLTF_IMAGE_FORMAT_COMPAT.get(normalized, "AUTO")

    @staticmethod
    @ensure_main_thread
    def export(
        scene: Any,
        objects: List[Any],
        filepath: str,
        preset: str = "game_engine",
        custom_settings: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Export to glTF 2.0 format.

        Args:
            preset: "web", "game_engine", "archviz"
            custom_settings: Override preset settings
        """
        if not BPY_AVAILABLE:
            return create_error(ErrorProtocol.NO_CONTEXT)

        try:
            # Ensure filepath has correct extension
            if filepath.lower().endswith(".gltf"):
                raise ValueError("Multi-file glTF export is disabled until sidecars are authorized")
            if not filepath.lower().endswith(".glb"):
                filepath += ".glb"
            settings = _MergeSafeExporterSettings(
                GLTFExporter.PRESETS.get(preset, GLTFExporter.PRESETS["game_engine"]),
                custom_settings,
                {"export_format": "GLB"},
            )
            PathDecision = GetFilesystemPolicy().RequirePath(
                filepath,
                FilesystemAccess.WRITE,
                {".glb"},
            )
            filepath = cast(str, PathDecision.ResolvedPath)

            # A GLB may embed bytes read from image paths stored in the scene.
            # The output grant does not authorize those independent reads.
            AuthorizeExternalImageInputs()
            PathDecision = GetFilesystemPolicy().RequirePath(
                filepath,
                FilesystemAccess.WRITE,
                {".glb"},
                CreateParents=True,
            )
            filepath = cast(str, PathDecision.ResolvedPath)

            # Normalize image format for Blender 5.0+ compatibility
            if "export_image_format" in settings:
                settings["export_image_format"] = GLTFExporter._normalize_image_format(
                    str(settings["export_image_format"])
                )

            # Safety: filter settings to only valid glTF exporter properties.
            # Unknown kwargs cause an immediate C++ crash in Blender 5.0+ (CRASH-02).
            # RNA introspection gives us the exact valid property set at runtime.
            try:
                valid_gltf_props = set(bpy.ops.export_scene.gltf.get_rna_type().properties.keys())
                filtered_settings = {k: v for k, v in settings.items() if k in valid_gltf_props}
                if len(filtered_settings) < len(settings):
                    removed = set(settings) - set(filtered_settings)
                    logger.warning(
                        f"GLTFExporter: removed unsupported params for this Blender version: {removed}"
                    )
            except Exception:
                filtered_settings = settings  # fallback: pass as-is

            # Select objects
            ContextManagerV3.deselect_all_objects()
            for obj in objects:
                if obj:
                    obj.select_set(True)

            # Export
            with ContextManagerV3.temp_override(
                area_type="VIEW_3D",
                active_object=objects[0] if objects else None,
            ):
                SafeOperators.export_gltf(
                    filepath=filepath, use_selection=True, **filtered_settings
                )

            # Get file size
            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            return {
                "success": True,
                "format": "glTF 2.0",
                "filepath": filepath,
                "preset": preset,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "objects_exported": len(objects),
            }

        except Exception as e:
            return create_error(
                ErrorProtocol.EXPORT_ERROR, custom_message=f"glTF export failed: {str(e)}"
            )

    @staticmethod
    def export_with_draco(
        scene: Any,
        objects: List[Any],
        filepath: str,
        quantization_bits: int = 14,
        compression_level: int = 7,
    ) -> Dict[str, Any]:
        """
        Export with Draco compression for web optimization.
        """
        # V1.0.0 Fix: Blender 5.0 API parameter drift
        is_b5 = getattr(getattr(bpy, "app", None), "version", (5, 0, 0)) >= (5, 0, 0)

        draco_settings = (
            {
                "export_draco_mesh_compression_enable": True,
                "export_draco_mesh_compression_level": compression_level,
                "export_draco_position_quantization": quantization_bits,
                "export_draco_normal_quantization": max(8, quantization_bits - 4),
                "export_draco_generic_quantization": max(8, quantization_bits - 2),
            }
            if is_b5
            else {
                "export_draco_mesh_compression_enable": True,
                "export_draco_mesh_compression_level": compression_level,
                "export_draco_position_quantization": quantization_bits,
                "export_draco_normal_quantization": max(8, quantization_bits - 4),
                "export_draco_generic_quantization": max(8, quantization_bits - 2),
                "use_draco_mesh_compression": True,  # Fallback for older versions
            }
        )

        return cast(
            Dict[str, Any],
            GLTFExporter.export(
                scene,
                objects,
                filepath,
                preset="web",
                custom_settings=draco_settings,
            ),
        )

    @staticmethod
    def validate_gltf(filepath: str) -> Dict[str, Any]:
        """
        Validate exported glTF file.

        Basic validation - for full validation use glTF Validator.
        """
        try:
            PathDecision = GetFilesystemPolicy().RequirePath(
                filepath,
                FilesystemAccess.READ,
                {".gltf", ".glb"},
            )
            filepath = cast(str, PathDecision.ResolvedPath)

            import json

            is_valid = True
            issues = []

            if filepath.endswith(".gltf"):
                # Check JSON structure
                with open(filepath, "r") as f:
                    try:
                        data = json.load(f)
                        if "asset" not in data:
                            is_valid = False
                            issues.append("Missing asset info")
                        if "meshes" not in data and "nodes" not in data:
                            is_valid = False
                            issues.append("No meshes or nodes found")
                    except json.JSONDecodeError:
                        is_valid = False
                        issues.append("Invalid JSON")

            elif filepath.endswith(".glb"):
                # Basic GLB header check
                with open(filepath, "rb") as f:
                    magic = f.read(4)
                    if magic != b"glTF":
                        is_valid = False
                        issues.append("Invalid GLB magic number")

            file_size = os.path.getsize(filepath)

            return {
                "success": True,
                "valid": is_valid,
                "issues": issues,
                "filepath": filepath,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
            }

        except Exception as e:
            return create_error(
                ErrorProtocol.EXECUTION_ERROR, custom_message=f"Validation failed: {str(e)}"
            )


class USDExporter:
    """
    USD (Universal Scene Description) exporter.

    Critical for:
    - Omniverse pipelines
    - Maya/Houdini interchange
    - Scene assembly
    """

    PRESETS = {
        "omniverse": {
            "export_materials": True,
            "generate_preview_surface": True,
        },
        "maya": {"export_materials": True, "convert_to_cm": True, "export_maya_collections": True},
        "houdini": {"export_materials": True, "export_subdiv": True, "export_houdini_attrs": True},
    }

    @staticmethod
    @ensure_main_thread
    def export(
        scene: Any,
        objects: List[Any],
        filepath: str,
        preset: str = "omniverse",
        custom_settings: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Export to USD format.

        Args:
            preset: "omniverse", "maya", "houdini"
        """
        if not BPY_AVAILABLE:
            return create_error(ErrorProtocol.NO_CONTEXT)

        try:
            # Ensure filepath has correct extension
            if not filepath.lower().endswith((".usd", ".usda", ".usdc", ".usdz")):
                filepath += ".usd"
            for SettingName in custom_settings or {}:
                if SettingName in {
                    "export_textures",
                    "export_textures_mode",
                    "overwrite_textures",
                    "convert_world_material",
                }:
                    raise ValueError("Output-family custom exporter settings are not allowed")
            TextureSettings = _GetUsdTextureSettings()
            settings = _MergeSafeExporterSettings(
                USDExporter.PRESETS.get(preset, USDExporter.PRESETS["omniverse"]),
                custom_settings,
                TextureSettings,
            )
            PathDecision = GetFilesystemPolicy().RequirePath(
                filepath,
                FilesystemAccess.WRITE,
                {".usd", ".usda", ".usdc", ".usdz"},
                CreateParents=True,
            )
            filepath = cast(str, PathDecision.ResolvedPath)

            # Select objects
            ContextManagerV3.deselect_all_objects()
            for obj in objects:
                if obj:
                    obj.select_set(True)

            # Export
            with ContextManagerV3.temp_override(
                area_type="VIEW_3D",
                active_object=objects[0] if objects else None,
            ):
                SafeOperators.export_usd(filepath=filepath, selected_objects_only=True, **settings)

            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            return {
                "success": True,
                "format": "USD",
                "filepath": filepath,
                "preset": preset,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "objects_exported": len(objects),
            }

        except Exception as e:
            return create_error(
                ErrorProtocol.EXPORT_ERROR, custom_message=f"USD export failed: {str(e)}"
            )


class AlembicExporter:
    """
    Alembic cache exporter for animation.
    """

    @staticmethod
    @ensure_main_thread
    def export_animation(
        scene: Any,
        objects: List[Any],
        filepath: str,
        frame_start: Optional[int] = None,
        frame_end: Optional[int] = None,
        frame_step: int = 1,
        export_uvs: bool = True,
        export_normals: bool = True,
        export_vcolors: bool = True,
        export_face_sets: bool = True,
        export_creases: bool = True,
        flatten_hierarchy: bool = False,
        visible_only: bool = True,
    ) -> Dict[str, Any]:
        """
        Export animation to Alembic cache.
        """
        if not BPY_AVAILABLE:
            return create_error(ErrorProtocol.NO_CONTEXT)

        try:
            # Ensure filepath has correct extension
            if not filepath.lower().endswith(".abc"):
                filepath += ".abc"
            PathDecision = GetFilesystemPolicy().RequirePath(
                filepath,
                FilesystemAccess.WRITE,
                {".abc"},
                CreateParents=True,
            )
            filepath = cast(str, PathDecision.ResolvedPath)

            # Use scene frame range if not specified
            if frame_start is None:
                frame_start = int(scene.frame_start)
            if frame_end is None:
                frame_end = int(scene.frame_end)

            # Select objects
            ContextManagerV3.deselect_all_objects()
            for obj in objects:
                if obj:
                    obj.select_set(True)

            # Export
            with ContextManagerV3.temp_override(
                area_type="VIEW_3D",
                active_object=objects[0] if objects else None,
            ):
                SafeOperators.export_alembic(
                    filepath=filepath,
                    selected=True,
                    start=frame_start,
                    end=frame_end,
                    frame_step=frame_step,
                    uvs=export_uvs,
                    normals=export_normals,
                    vcolors=export_vcolors,
                    face_sets=export_face_sets,
                    subdiv_schema=export_creases,
                    flatten=flatten_hierarchy,
                    visible_objects_only=visible_only,
                )

            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            frame_count = (frame_end - frame_start) // frame_step + 1

            return {
                "success": True,
                "format": "Alembic",
                "filepath": filepath,
                "frame_range": (frame_start, frame_end),
                "frame_step": frame_step,
                "frames": frame_count,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "objects_exported": len(objects),
            }

        except Exception as e:
            return create_error(
                ErrorProtocol.EXPORT_ERROR, custom_message=f"Alembic export failed: {str(e)}"
            )


class FBXExporter:
    """
    FBX exporter for game engine interchange.
    """

    PRESETS = {
        "unity": {
            "axis_forward": "-Z",
            "axis_up": "Y",
            "bake_space_transform": True,
            "use_mesh_edges": False,
            "use_tspace": True,
        },
        "unreal": {
            "axis_forward": "-Z",
            "axis_up": "Y",
            "bake_space_transform": False,
            "use_mesh_edges": False,
            "use_tspace": True,
            "add_leaf_bones": False,
        },
        "maya": {
            "axis_forward": "-Z",
            "axis_up": "Y",
            "bake_space_transform": False,
            "use_mesh_edges": True,
        },
    }

    @staticmethod
    @ensure_main_thread
    def export(
        objects: List[Any],
        filepath: str,
        preset: str = "unity",
        custom_settings: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Export to FBX format.

        Args:
            preset: "unity", "unreal", "maya"
        """
        if not BPY_AVAILABLE:
            return create_error(ErrorProtocol.NO_CONTEXT)

        try:
            # Ensure filepath has correct extension
            if not filepath.lower().endswith(".fbx"):
                filepath += ".fbx"
            settings = _MergeSafeExporterSettings(
                FBXExporter.PRESETS.get(preset, FBXExporter.PRESETS["unity"]),
                custom_settings,
                {"path_mode": "STRIP"},
            )
            PathDecision = GetFilesystemPolicy().RequirePath(
                filepath,
                FilesystemAccess.WRITE,
                {".fbx"},
                CreateParents=True,
            )
            filepath = cast(str, PathDecision.ResolvedPath)

            # Fix: FBX requires actual ViewLayer manipulation, Context Override is unreliable
            view_layer = bpy.context.view_layer
            original_active = view_layer.objects.active
            # Copy the list of currently selected objects
            original_selected = list(bpy.context.selected_objects)

            try:
                # Deselect all physically
                ContextManagerV3.deselect_all_objects()

                # Select only requested objects
                for obj in objects:
                    if obj:
                        obj.select_set(True)

                if objects:
                    view_layer.objects.active = objects[0]

                # Export
                SafeOperators.export_fbx(filepath=filepath, use_selection=True, **settings)

            finally:
                # Restore previous selection state
                ContextManagerV3.deselect_all_objects()
                for obj in original_selected:
                    try:
                        obj.select_set(True)
                    except Exception:
                        pass
                if original_active:
                    try:
                        view_layer.objects.active = original_active
                    except Exception:
                        pass

            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

            return {
                "success": True,
                "format": "FBX",
                "filepath": filepath,
                "preset": preset,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "objects_exported": len(objects),
            }

        except Exception as e:
            return create_error(
                ErrorProtocol.EXPORT_ERROR, custom_message=f"FBX export failed: {str(e)}"
            )


class BatchExporter:
    """
    Batch export to multiple formats.
    """

    @staticmethod
    @ensure_main_thread
    def export_all_formats(
        scene: Any,
        objects: List[Any],
        base_path: str,
        formats: Optional[List[str]] = None,
        presets: Optional[Dict[str, str]] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        Export to multiple formats in one operation.

        Args:
            formats: List of formats ["GLB", "FBX", "USD"]
            presets: Dict of format->preset mappings
        """
        if not BPY_AVAILABLE:
            return create_error(ErrorProtocol.NO_CONTEXT)

        try:
            if formats is None:
                formats = ["GLB", "FBX", "USD"]

            if presets is None:
                presets = {}

            NormalizedFormats = [Format.upper() for Format in formats]
            if "GLB" in NormalizedFormats:
                AuthorizeExternalImageInputs()

            results = []
            errors = []

            base = Path(base_path)
            ExtensionByFormat = {
                "GLB": ".glb",
                "USD": ".usd",
                "FBX": ".fbx",
                "OBJ": ".obj",
            }

            for fmt in NormalizedFormats:
                fmt = fmt.upper()
                Extension = ExtensionByFormat.get(fmt)
                if Extension is None:
                    errors.append(f"Unsupported format: {fmt}")
                    continue
                filepath = str(base.with_suffix(Extension))
                presets.get(fmt, "default")

                try:
                    PathDecision = GetFilesystemPolicy().RequirePath(
                        filepath,
                        FilesystemAccess.WRITE,
                        {Extension},
                        CreateParents=True,
                    )
                    filepath = cast(str, PathDecision.ResolvedPath)
                    if os.path.exists(filepath) and not overwrite:
                        errors.append(f"{fmt}: target exists and overwrite was not requested")
                        continue

                    if fmt == "GLB":
                        result = GLTFExporter.export(
                            scene, objects, filepath, preset=presets.get(fmt, "game_engine")
                        )
                    elif fmt == "USD":
                        result = USDExporter.export(
                            scene, objects, filepath, preset=presets.get(fmt, "omniverse")
                        )
                    elif fmt == "FBX":
                        result = FBXExporter.export(
                            objects, filepath, preset=presets.get(fmt, "unity")
                        )
                    elif fmt == "OBJ":
                        result = BatchExporter._export_obj(objects, filepath)

                    if "error" in result:
                        errors.append(f"{fmt}: {result['error']}")
                    else:
                        results.append(result)

                except Exception as e:
                    errors.append(f"{fmt}: {str(e)}")

            return {
                "success": len(results) > 0,
                "exports": results,
                "errors": errors,
                "successful_exports": len(results),
                "failed_exports": len(errors),
            }

        except Exception as e:
            return create_error(
                ErrorProtocol.EXPORT_ERROR, custom_message=f"Batch export failed: {str(e)}"
            )

    @staticmethod
    @ensure_main_thread
    def _export_obj(objects: List[Any], filepath: str) -> Dict[str, Any]:
        """Internal OBJ export helper.

        Blender 4.0 replaced bpy.ops.export_scene.obj with bpy.ops.wm.obj_export.
        Parameter names also changed: use_selection→export_selected_objects,
        use_uvs→export_uv, use_normals→export_normals, use_materials→export_materials,
        use_triangles→export_triangulated_mesh.
        SafeOperators.export_obj tries wm.obj_export first; we pass the new-style params.
        """
        if not filepath.lower().endswith(".obj"):
            filepath += ".obj"
        PathDecision = GetFilesystemPolicy().RequirePath(
            filepath,
            FilesystemAccess.WRITE,
            {".obj"},
            CreateParents=True,
        )
        filepath = cast(str, PathDecision.ResolvedPath)

        ContextManagerV3.deselect_all_objects()
        for obj in objects:
            if obj:
                obj.select_set(True)
        with ContextManagerV3.temp_override(
            area_type="VIEW_3D",
            active_object=objects[0] if objects else None,
        ):
            # Check which OBJ operator is available and use correct parameter names.
            wm_ops = getattr(bpy.ops, "wm", None) if BPY_AVAILABLE else None
            if wm_ops is not None and hasattr(wm_ops, "obj_export"):
                # Blender 4.0+ — new operator + new parameter names
                SafeOperators.export_obj(
                    filepath=filepath,
                    export_selected_objects=True,
                    export_materials=False,
                    export_triangulated_mesh=False,
                    export_normals=True,
                    export_uv=True,
                )
            else:
                # Blender 3.x legacy operator + old parameter names
                SafeOperators.export_obj(
                    filepath=filepath,
                    use_selection=True,
                    use_materials=False,
                    use_triangles=False,
                    use_normals=True,
                    use_uvs=True,
                )

        file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

        return {
            "success": True,
            "format": "OBJ",
            "filepath": filepath,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
        }


class ExportValidator:
    """
    Validate export settings and scene readiness.
    V13: Includes Export Armor (Fail-Fast Interceptors).
    """

    @staticmethod
    def check_geometry_complexity(objects: List[Any], vertex_limit: int = 5_000_000) -> None:
        """
        PRE-FLIGHT INTERCEPTOR: Prevent Out-Of-Memory system lockouts.
        Raises ValueError if total geometry exceeds the limit.
        """
        total_vertices = 0
        for obj in objects:
            if obj and getattr(obj, "type", "") == "MESH" and obj.data:
                # Mesh might not have evaluated geometry cleanly, rough check:
                total_vertices += len(getattr(obj.data, "vertices", []))

        if total_vertices > vertex_limit:
            raise ValueError(
                f"[EXPORT ARMOR] Geometry complexity too high: {total_vertices} vertices. "
                "Agent Action Required: You MUST apply SMART_DECIMATE on high-poly objects "
                "before attempting to export. Export aborted to prevent OOM crash."
            )

    @staticmethod
    def validate_for_export(
        objects: List[Any], export_format: str, force_export: bool = False
    ) -> Dict[str, Any]:
        """
        Validate objects are ready for export.
        Added Export Armor guards.
        """
        issues = []
        warnings = []

        if not objects:
            issues.append("No objects selected for export")

        # 1) Export Armor: Geometry Complexity Check
        del force_export
        ExportValidator.check_geometry_complexity(objects)

        for obj in objects:
            if getattr(obj, "type", "") == "MESH":
                mesh = obj.data
                # Check for ngons in game formats
                if export_format in ["FBX", "GLB", "GLTF"]:
                    for poly in getattr(mesh, "polygons", []):
                        if len(poly.vertices) > 4:
                            warnings.append(f"{obj.name} has ngons (may cause issues)")
                            break

                # Check for scale issues
                scale = getattr(obj, "scale", [1.0, 1.0, 1.0])
                if any(abs(s - 1.0) > 0.01 for s in scale):
                    warnings.append(f"{obj.name} has non-uniform scale: {scale}")

                # Check for negative scale
                if any(s < 0 for s in scale):
                    issues.append(f"{obj.name} has negative scale (will flip normals)")

                # Check UVs
                if export_format in ["GLB", "GLTF", "FBX"]:
                    if not getattr(mesh, "uv_layers", None):
                        warnings.append(f"{obj.name} has no UVs")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "objects_checked": len(objects),
        }

    @staticmethod
    def check_path_injection(filepath: str, force_export: bool = False) -> None:
        """
        PRE-FLIGHT INTERCEPTOR: Prevent Path Traversal (LFI) attacks.
        Raises ValueError if the path tries to escape the allowed workspace context.
        """
        del force_export
        GetFilesystemPolicy().RequirePath(
            filepath,
            FilesystemAccess.WRITE,
            CreateParents=False,
        )

    @staticmethod
    def check_export_path(
        filepath: str, overwrite: bool = False, force_export: bool = False
    ) -> Dict[str, Any]:
        """
        Check export path is valid.
        """
        # 1) Export Armor: Security Check
        del force_export
        PathDecision = GetFilesystemPolicy().RequirePath(
            filepath,
            FilesystemAccess.WRITE,
            CreateParents=False,
        )
        AuthorizedPath = cast(str, PathDecision.ResolvedPath)

        issues = []

        # Check file exists
        if os.path.exists(AuthorizedPath) and not overwrite:
            issues.append("Target exists and overwrite was not requested")

        # Check extension
        valid_extensions = {
            ".gltf",
            ".glb",
            ".usd",
            ".usda",
            ".usdc",
            ".usdz",
            ".abc",
            ".fbx",
            ".obj",
            ".stl",
            ".ply",
            ".x3d",
        }
        ext = os.path.splitext(AuthorizedPath)[1].lower()
        if ext not in valid_extensions:
            issues.append("Unknown or missing export extension")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "filepath": AuthorizedPath,
        }


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "AuthorizeExternalImageInputs",
    "GLTFExporter",
    "USDExporter",
    "AlembicExporter",
    "FBXExporter",
    "BatchExporter",
    "ExportValidator",
    "ExportFormat",
    "ExportSettings",
]
