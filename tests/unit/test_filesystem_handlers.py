"""Regression tests for filesystem-aware scene and export routes."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from blender_mcp.core.enums import (
    AdvancedBatchAction,
    CloudRenderAction,
    ExportAction,
    ExportPipelineAction,
    HeadlessModeAction,
    LightAction,
    MocapAction,
    RenderAction,
    SceneAction,
    SequencerAction,
    UVsAction,
)
from blender_mcp.core.export_pipeline import (
    BatchExporter,
    ExportValidator,
    GLTFExporter,
    USDExporter,
)
from blender_mcp.core.filesystem_boundary import ConfigureFilesystemPolicy, ResetFilesystemPolicy
from blender_mcp.core.security import Capability, SecurityManager
from blender_mcp.dispatcher import (
    HANDLER_METADATA,
    HANDLER_REGISTRY,
    dispatch_command,
    load_handlers,
)
from blender_mcp.handlers import manage_advanced_batch as AdvancedBatchModule
from blender_mcp.handlers import manage_cloud_render as CloudRenderModule
from blender_mcp.handlers import manage_export as StandardExportModule
from blender_mcp.handlers import manage_export_pipeline as ExportPipelineModule
from blender_mcp.handlers import manage_headless_mode as HeadlessHandlerModule
from blender_mcp.handlers import manage_light as LightModule
from blender_mcp.handlers import manage_mocap as MocapModule
from blender_mcp.handlers import manage_rendering as RenderingModule
from blender_mcp.handlers import manage_scene as SceneModule
from blender_mcp.handlers import manage_sequencer as SequencerModule
from blender_mcp.handlers import manage_uvs as UVModule
from blender_mcp.handlers import unity_export as UnityModule
import blender_mcp.core.export_pipeline as ExportCoreModule
import blender_mcp.core.blender50_features as Blender50FeaturesModule
import blender_mcp.core.headless_mode as HeadlessCoreModule


load_handlers()


@pytest.fixture(autouse=True)
def ResetPolicy() -> None:
    ResetFilesystemPolicy()
    yield
    ResetFilesystemPolicy()


def ErrorCode(Result: dict) -> str:
    return str(Result["errors"][0]["code"])


def test_scene_open_denies_outside_root_before_blender_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ReadRoot = tmp_path / "read"
    ReadRoot.mkdir()
    Outside = tmp_path / "outside.blend"
    Outside.write_bytes(b"sentinel")
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot)
    Execute = MagicMock(side_effect=AssertionError("main-thread operation should not run"))
    monkeypatch.setattr(SceneModule, "execute_on_main_thread", Execute)

    Result = SceneModule._handle_open_file(filepath=str(Outside))

    assert Result["success"] is False
    assert ErrorCode(Result) == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    Execute.assert_not_called()


def test_scene_open_preserves_legitimate_inside_root_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ReadRoot = tmp_path / "read"
    ReadRoot.mkdir()
    ScenePath = ReadRoot / "scene.blend"
    ScenePath.write_bytes(b"BLENDER")
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot)
    OpenMainFile = MagicMock()
    OpenMainFile.return_value = SimpleNamespace(success=True)
    monkeypatch.setattr(
        SceneModule, "safe_ops", SimpleNamespace(wm=SimpleNamespace(open_mainfile=OpenMainFile))
    )
    monkeypatch.setattr(
        SceneModule, "execute_on_main_thread", lambda Function, **_Kwargs: Function()
    )

    Result = SceneModule._handle_open_file(filepath=str(ScenePath))

    assert Result["success"] is True
    OpenMainFile.assert_called_once_with(filepath=str(ScenePath.resolve()))


def test_scene_save_does_not_overwrite_without_local_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Target = tmp_path / "scene.blend"
    Target.write_bytes(b"sentinel")
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Execute = MagicMock(side_effect=AssertionError("save operation should not run"))
    monkeypatch.setattr(SceneModule, "execute_on_main_thread", Execute)

    Result = SceneModule._handle_save_file(filepath=str(Target))

    assert Result["success"] is False
    assert ErrorCode(Result) == "FILESYSTEM_OVERWRITE_DENIED"
    assert Target.read_bytes() == b"sentinel"
    Execute.assert_not_called()


def test_scene_save_revalidates_current_file_and_preserves_approved_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Target = tmp_path / "scene.blend"
    Target.write_bytes(b"sentinel")
    ConfigureFilesystemPolicy(WriteRoot=tmp_path, AllowOverwrite=True)
    SaveMainFile = MagicMock()
    SaveMainFile.return_value = SimpleNamespace(success=True)
    monkeypatch.setattr(SceneModule.bpy.data, "filepath", str(Target))
    monkeypatch.setattr(
        SceneModule, "safe_ops", SimpleNamespace(wm=SimpleNamespace(save_mainfile=SaveMainFile))
    )
    monkeypatch.setattr(
        SceneModule, "execute_on_main_thread", lambda Function, **_Kwargs: Function()
    )

    Result = SceneModule._handle_save_file()

    assert Result["success"] is True
    SaveMainFile.assert_called_once_with()


def test_force_export_cannot_bypass_policy() -> None:
    Result = ExportPipelineModule.manage_export_pipeline(
        action="CHECK_EXPORT_PATH",
        filepath="outside.glb",
        force_export=True,
    )

    assert Result["success"] is False
    assert ErrorCode(Result) == "FORCE_EXPORT_DENIED"


def test_export_validator_denies_sibling_prefix_and_redacts_path(tmp_path: Path) -> None:
    WriteRoot = tmp_path / "project"
    WriteRoot.mkdir()
    ConfigureFilesystemPolicy(WriteRoot=WriteRoot)
    Outside = tmp_path / "project-escape" / "secret.glb"

    with pytest.raises(ValueError) as Captured:
        ExportValidator.check_export_path(str(Outside))

    assert str(Outside) not in str(Captured.value)


def test_final_export_extension_is_checked_before_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WriteRoot = tmp_path / "write"
    WriteRoot.mkdir()
    ConfigureFilesystemPolicy(WriteRoot=WriteRoot)
    Export = MagicMock(side_effect=AssertionError("export operator should not run"))
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_gltf", Export)

    Result = GLTFExporter.export(MagicMock(), [], str(tmp_path / "outside" / "hero"))

    assert Result["code"] == "EXPORT_ERROR"
    assert "outside the user-approved root" in Result["message"]
    Export.assert_not_called()


def test_extension_appending_preserves_inside_root_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Export = MagicMock()
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_gltf", Export)
    monkeypatch.setattr(ExportCoreModule.ContextManagerV3, "deselect_all_objects", MagicMock())
    monkeypatch.setattr(
        ExportCoreModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )

    Result = GLTFExporter.export(MagicMock(), [], "nested/hero")

    Expected = str((tmp_path / "nested" / "hero.glb").resolve())
    assert Result["success"] is True
    assert Result["filepath"] == Expected
    Export.assert_called_once()
    assert Export.call_args.kwargs["filepath"] == Expected


def test_standard_export_enforces_action_specific_extension_before_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Target = tmp_path / "valuable.blend"
    Target.write_bytes(b"sentinel")
    ConfigureFilesystemPolicy(WriteRoot=tmp_path, AllowOverwrite=True)
    Export = MagicMock(side_effect=AssertionError("export operator should not run"))
    monkeypatch.setattr(
        StandardExportModule,
        "safe_ops",
        SimpleNamespace(export_scene=SimpleNamespace(fbx=Export)),
    )

    Result = StandardExportModule.manage_export(
        action=ExportAction.EXPORT_FBX.value,
        filepath=str(Target),
        safe_mode=False,
    )

    assert Result["success"] is False
    assert ErrorCode(Result) == "INVALID_PATH"
    assert Target.read_bytes() == b"sentinel"
    Export.assert_not_called()


def test_gltf_rejects_multifile_extension_and_path_bearing_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Export = MagicMock(side_effect=AssertionError("export operator should not run"))
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_gltf", Export)

    ExtensionResult = GLTFExporter.export(MagicMock(), [], str(tmp_path / "hero.gltf"))
    SettingResult = GLTFExporter.export(
        MagicMock(),
        [],
        str(tmp_path / "custom" / "hero.glb"),
        custom_settings={"export_texture_dir": str(tmp_path / "textures")},
    )
    FormatResult = GLTFExporter.export(
        MagicMock(),
        [],
        str(tmp_path / "hero.glb"),
        custom_settings={"export_format": "GLTF_SEPARATE"},
    )

    assert ExtensionResult["code"] == "EXPORT_ERROR"
    assert SettingResult["code"] == "EXPORT_ERROR"
    assert FormatResult["code"] == "EXPORT_ERROR"
    assert not (tmp_path / "custom").exists()
    Export.assert_not_called()


def test_gltf_authorizes_external_images_before_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ReadRoot = tmp_path / "read"
    WriteRoot = tmp_path / "write"
    OutsideRoot = tmp_path / "outside"
    for Directory in (ReadRoot, WriteRoot, OutsideRoot):
        Directory.mkdir()
    OutsideImage = OutsideRoot / "secret.png"
    OutsideImage.write_bytes(b"not-an-image")
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot, WriteRoot=WriteRoot)
    Image = SimpleNamespace(
        source="FILE",
        filepath=str(OutsideImage),
        packed_file=None,
        packed_files=[],
        library=None,
    )
    monkeypatch.setattr(ExportCoreModule.bpy.data, "images", [Image])
    monkeypatch.setattr(
        ExportCoreModule.bpy,
        "path",
        SimpleNamespace(abspath=lambda FilePath, **_Kwargs: FilePath),
    )
    Export = MagicMock(side_effect=AssertionError("export operator should not run"))
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_gltf", Export)

    Result = GLTFExporter.export(MagicMock(), [], str(WriteRoot / "nested" / "hero.glb"))

    assert Result["code"] == "EXPORT_ERROR"
    assert "outside the user-approved root" in Result["message"]
    assert str(OutsideImage) not in Result["message"]
    assert not (WriteRoot / "nested").exists()
    Export.assert_not_called()


@pytest.mark.parametrize("Source", ["TILED", "SEQUENCE", "MOVIE"])
@pytest.mark.parametrize(
    ("PackedFile", "PackedFiles"),
    [(None, []), (object(), []), (None, [object()])],
)
def test_gltf_denies_unbounded_image_input_families_before_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    Source: str,
    PackedFile: object | None,
    PackedFiles: list[object],
) -> None:
    ConfigureFilesystemPolicy(ReadRoot=tmp_path, WriteRoot=tmp_path)
    Image = SimpleNamespace(
        source=Source,
        filepath="tiles_<UDIM>.png",
        packed_file=PackedFile,
        packed_files=PackedFiles,
    )
    monkeypatch.setattr(ExportCoreModule.bpy.data, "images", [Image])
    Export = MagicMock(side_effect=AssertionError("export operator should not run"))
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_gltf", Export)

    Result = GLTFExporter.export(MagicMock(), [], str(tmp_path / "output" / "hero.glb"))

    assert Result["code"] == "EXPORT_ERROR"
    assert "complete input family" in Result["message"]
    assert not (tmp_path / "output").exists()
    Export.assert_not_called()


def test_gltf_allows_packed_file_image_without_external_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Image = SimpleNamespace(
        source="FILE",
        filepath=str(tmp_path.parent / "outside" / "unused.png"),
        packed_file=object(),
        packed_files=[],
    )
    monkeypatch.setattr(ExportCoreModule.bpy.data, "images", [Image])
    monkeypatch.setattr(ExportCoreModule.ContextManagerV3, "deselect_all_objects", MagicMock())
    monkeypatch.setattr(
        ExportCoreModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )
    Export = MagicMock()
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_gltf", Export)

    Result = GLTFExporter.export(MagicMock(), [], str(tmp_path / "hero.glb"))

    assert Result["success"] is True
    Export.assert_called_once()


def test_usd_disables_texture_sidecars_and_path_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Export = MagicMock()
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_usd", Export)
    monkeypatch.setattr(
        ExportCoreModule,
        "_GetUsdTextureSettings",
        lambda: {
            "export_textures_mode": "KEEP",
            "overwrite_textures": False,
            "convert_world_material": False,
        },
    )
    monkeypatch.setattr(ExportCoreModule.ContextManagerV3, "deselect_all_objects", MagicMock())
    monkeypatch.setattr(
        ExportCoreModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )

    Result = USDExporter.export(MagicMock(), [], str(tmp_path / "hero.usd"))
    PathResult = USDExporter.export(
        MagicMock(),
        [],
        str(tmp_path / "other.usd"),
        custom_settings={"relative_paths": True},
    )
    ModeResult = USDExporter.export(
        MagicMock(),
        [],
        str(tmp_path / "mode.usd"),
        custom_settings={"export_textures_mode": "NEW"},
    )

    assert Result["success"] is True
    assert PathResult["code"] == "EXPORT_ERROR"
    assert ModeResult["code"] == "EXPORT_ERROR"
    assert Export.call_count == 1
    assert Export.call_args.kwargs["export_textures_mode"] == "KEEP"
    assert Export.call_args.kwargs["overwrite_textures"] is False
    assert Export.call_args.kwargs["convert_world_material"] is False


def test_obj_export_disables_material_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Export = MagicMock()
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_obj", Export)
    monkeypatch.setattr(ExportCoreModule.ContextManagerV3, "deselect_all_objects", MagicMock())
    monkeypatch.setattr(
        ExportCoreModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        ExportCoreModule.bpy.ops,
        "wm",
        SimpleNamespace(obj_export=MagicMock()),
    )

    Result = BatchExporter._export_obj([], str(tmp_path / "hero.obj"))

    assert Result["success"] is True
    assert Export.call_args.kwargs["export_materials"] is False


def test_standard_and_advanced_obj_routes_disable_material_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    StandardExport = MagicMock()
    AdvancedExport = MagicMock()
    WmOps = SimpleNamespace(obj_export=MagicMock())
    monkeypatch.setattr(StandardExportModule.bpy.ops, "wm", WmOps)
    monkeypatch.setattr(StandardExportModule.SafeOperators, "export_obj", StandardExport)
    monkeypatch.setattr(
        StandardExportModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )

    StandardResult = StandardExportModule.manage_export(
        action=ExportAction.EXPORT_OBJ.value,
        filepath=str(tmp_path / "standard" / "hero.obj"),
        safe_mode=False,
    )

    Obj = MagicMock()
    Obj.name = "Hero"
    monkeypatch.setattr(AdvancedBatchModule.bpy.data.objects, "get", MagicMock(return_value=Obj))
    monkeypatch.setattr(
        AdvancedBatchModule,
        "safe_ops",
        SimpleNamespace(wm=SimpleNamespace(obj_export=AdvancedExport)),
    )
    monkeypatch.setattr(
        AdvancedBatchModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )
    monkeypatch.setattr(AdvancedBatchModule.ContextManagerV3, "deselect_all_objects", MagicMock())

    AdvancedResult = AdvancedBatchModule._export_batch_variants(
        {
            "objects": ["Hero"],
            "base_path": str(tmp_path / "advanced"),
            "formats": ["OBJ"],
        }
    )

    assert StandardResult["success"] is True
    assert StandardExport.call_args.kwargs["export_materials"] is False
    assert AdvancedResult["success"] is True
    assert AdvancedExport.call_args.kwargs["export_materials"] is False


def test_force_export_no_longer_skips_geometry_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    CheckGeometry = MagicMock()
    monkeypatch.setattr(ExportValidator, "check_geometry_complexity", CheckGeometry)

    ExportValidator.validate_for_export([], "GLB", force_export=True)

    CheckGeometry.assert_called_once_with([])


def test_advanced_batch_denies_path_before_selection_or_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WriteRoot = tmp_path / "write"
    WriteRoot.mkdir()
    ConfigureFilesystemPolicy(WriteRoot=WriteRoot)
    Obj = MagicMock()
    monkeypatch.setattr(AdvancedBatchModule.bpy.data.objects, "get", MagicMock(return_value=Obj))
    Export = MagicMock()
    monkeypatch.setattr(
        AdvancedBatchModule,
        "safe_ops",
        SimpleNamespace(export_scene=SimpleNamespace(gltf=Export)),
    )

    Result = AdvancedBatchModule._export_batch_variants(
        {
            "objects": ["Hero"],
            "base_path": str(tmp_path / "outside"),
            "formats": ["GLTF"],
        }
    )

    assert Result["success"] is False
    assert ErrorCode(Result) == "INVALID_PATH"
    Obj.select_set.assert_not_called()
    Export.assert_not_called()


def test_adjacent_uv_unity_and_cloud_routes_fail_before_file_sinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WriteRoot = tmp_path / "write"
    WriteRoot.mkdir()
    ConfigureFilesystemPolicy(WriteRoot=WriteRoot)
    Outside = tmp_path / "outside"
    UVExport = MagicMock()
    UnityExport = MagicMock()
    PackAll = MagicMock()
    monkeypatch.setattr(
        UVModule, "safe_ops", SimpleNamespace(uv=SimpleNamespace(export_layout=UVExport))
    )
    monkeypatch.setattr(
        UnityModule,
        "safe_ops",
        SimpleNamespace(export_scene=SimpleNamespace(fbx=UnityExport)),
    )
    monkeypatch.setattr(
        CloudRenderModule,
        "safe_ops",
        SimpleNamespace(file=SimpleNamespace(pack_all=PackAll)),
    )
    monkeypatch.setattr(CloudRenderModule.bpy.data, "filepath", str(tmp_path / "source.blend"))

    UVResult = UVModule._handle_export_layout(MagicMock(), {"filepath": str(Outside / "uv.png")})
    UnityResult = UnityModule.export_unity_fbx(filepath=str(Outside / "hero.fbx"))
    CloudResult = CloudRenderModule._package_assets({"output_dir": str(Outside)})

    assert ErrorCode(UVResult) == "INVALID_PATH"
    assert "outside the user-approved root" in UnityResult["error"]
    assert ErrorCode(CloudResult) == "ASSET_PACKING_DISABLED"
    UVExport.assert_not_called()
    UnityExport.assert_not_called()
    PackAll.assert_not_called()


def test_cloud_packaging_is_quarantined_and_optimization_does_not_pack_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ScenePath = tmp_path / "scene.blend"
    ScenePath.write_bytes(b"sentinel")
    ConfigureFilesystemPolicy(WriteRoot=tmp_path, AllowOverwrite=True)
    PackAll = MagicMock(side_effect=AssertionError("asset reads are not authorized"))
    Save = MagicMock()
    monkeypatch.setattr(
        CloudRenderModule,
        "bpy",
        SimpleNamespace(
            context=SimpleNamespace(scene=SimpleNamespace(objects=[])),
            data=SimpleNamespace(filepath=str(ScenePath), images=[]),
        ),
    )
    monkeypatch.setattr(
        CloudRenderModule,
        "safe_ops",
        SimpleNamespace(
            file=SimpleNamespace(pack_all=PackAll),
            wm=SimpleNamespace(save_as_mainfile=Save),
        ),
    )
    monkeypatch.setattr(
        CloudRenderModule.ContextManagerV3,
        "temp_override",
        lambda **_Kwargs: nullcontext(),
    )

    PackageResult = CloudRenderModule._package_assets({"output_dir": str(tmp_path)})
    OptimizeResult = CloudRenderModule._optimize_for_farm({})

    assert ErrorCode(PackageResult) == "ASSET_PACKING_DISABLED"
    assert OptimizeResult["success"] is True
    assert OptimizeResult["data"]["warnings"]
    PackAll.assert_not_called()
    Save.assert_called_once_with(filepath=str(ScenePath.resolve()))


def test_sequencer_denies_media_path_before_editor_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ReadRoot = tmp_path / "media"
    ReadRoot.mkdir()
    Outside = tmp_path / "outside.mp4"
    Outside.write_bytes(b"media")
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot)
    EnsureEditor = MagicMock(side_effect=AssertionError("editor should not be created"))
    monkeypatch.setattr(SequencerModule, "_ensure_sequencer_editor", EnsureEditor)

    Result = SequencerModule.manage_sequencer(
        action=SequencerAction.ADD_MOVIE.value,
        filepath=str(Outside),
    )

    assert Result["success"] is False
    assert ErrorCode(Result) == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    EnsureEditor.assert_not_called()


def test_sequencer_media_read_preserves_inside_root_behavior(tmp_path: Path) -> None:
    Media = tmp_path / "clip.mp4"
    Media.write_bytes(b"media")
    ConfigureFilesystemPolicy(ReadRoot=tmp_path)
    Strip = SimpleNamespace(
        name="clip.mp4",
        type="MOVIE",
        channel=1,
        frame_start=1,
        frame_final_duration=24,
    )
    NewMovie = MagicMock(return_value=Strip)
    Editor = SimpleNamespace(sequences=SimpleNamespace(new_movie=NewMovie))

    Result = SequencerModule._handle_add_movie(
        Editor,
        {"filepath": str(Media), "channel": 1, "frame_start": 1},
    )

    assert Result["success"] is True
    NewMovie.assert_called_once_with(
        name="clip.mp4",
        filepath=str(Media.resolve()),
        channel=1,
        frame_start=1,
    )


def test_sequencer_preview_output_family_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    EnsureEditor = MagicMock(side_effect=AssertionError("editor should not be created"))
    monkeypatch.setattr(SequencerModule, "_ensure_sequencer_editor", EnsureEditor)

    Result = SequencerModule.manage_sequencer(
        action=SequencerAction.RENDER_PREVIEW.value,
        filepath="preview_####",
    )

    assert Result["success"] is False
    assert ErrorCode(Result) == "OUTPUT_FAMILY_DISABLED"
    EnsureEditor.assert_not_called()


def test_hdri_denies_outside_root_before_image_or_world_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ReadRoot = tmp_path / "environment"
    ReadRoot.mkdir()
    Outside = tmp_path / "outside.hdr"
    Outside.write_bytes(b"environment")
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot)
    LoadImage = MagicMock(side_effect=AssertionError("image should not be loaded"))
    World = SimpleNamespace(use_nodes=False)
    monkeypatch.setattr(
        LightModule,
        "bpy",
        SimpleNamespace(
            context=SimpleNamespace(scene=SimpleNamespace(world=World)),
            data=SimpleNamespace(images=SimpleNamespace(load=LoadImage)),
        ),
    )

    Result = LightModule.manage_light(
        action=LightAction.SETUP_HDRI.value,
        filepath=str(Outside),
    )

    assert Result["success"] is False
    assert ErrorCode(Result) == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    assert World.use_nodes is False
    LoadImage.assert_not_called()


def test_hdri_read_preserves_authorized_environment_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Environment = tmp_path / "studio.exr"
    Environment.write_bytes(b"environment")
    ConfigureFilesystemPolicy(ReadRoot=tmp_path)
    Image = object()
    LoadImage = MagicMock(return_value=Image)
    Background = SimpleNamespace(
        inputs={
            "Color": object(),
            "Strength": SimpleNamespace(default_value=None),
        },
        outputs={"Background": object(), "Color": object()},
    )
    Output = SimpleNamespace(inputs={"Surface": object()})
    EnvironmentNode = SimpleNamespace(image=None, outputs={"Color": object()})
    Nodes = MagicMock()
    Nodes.new.side_effect = [Background, Output, EnvironmentNode]
    Links = SimpleNamespace(new=MagicMock())
    World = SimpleNamespace(
        name="World",
        use_nodes=False,
        node_tree=SimpleNamespace(nodes=Nodes, links=Links),
    )
    monkeypatch.setattr(
        LightModule,
        "bpy",
        SimpleNamespace(
            context=SimpleNamespace(scene=SimpleNamespace(world=World)),
            data=SimpleNamespace(
                images=SimpleNamespace(load=LoadImage),
                worlds=SimpleNamespace(new=MagicMock()),
            ),
        ),
    )

    Result = LightModule.manage_light(
        action=LightAction.SETUP_HDRI.value,
        filepath=str(Environment),
        energy=2.0,
    )

    assert Result["success"] is True
    assert World.use_nodes is True
    assert EnvironmentNode.image is Image
    assert Background.inputs["Strength"].default_value == 2.0
    LoadImage.assert_called_once_with(str(Environment.resolve()), check_existing=True)


def test_hdri_error_is_redacted_and_extension_is_restricted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Png = tmp_path / "secret-name.png"
    Png.write_bytes(b"image")
    Exr = tmp_path / "secret-name.exr"
    Exr.write_bytes(b"environment")
    ConfigureFilesystemPolicy(ReadRoot=tmp_path)
    LoadImage = MagicMock(side_effect=RuntimeError(f"decoder leaked {Exr}"))
    monkeypatch.setattr(
        LightModule,
        "bpy",
        SimpleNamespace(
            context=SimpleNamespace(scene=SimpleNamespace(world=None)),
            data=SimpleNamespace(images=SimpleNamespace(load=LoadImage)),
        ),
    )

    ExtensionResult = LightModule.manage_light(
        action=LightAction.SETUP_HDRI.value,
        filepath=str(Png),
    )
    LoadResult = LightModule.manage_light(
        action=LightAction.SETUP_HDRI.value,
        filepath=str(Exr),
    )

    assert ErrorCode(ExtensionResult) == "FILESYSTEM_EXTENSION_DENIED"
    assert ErrorCode(LoadResult) == "EXECUTION_ERROR"
    assert str(Exr) not in str(LoadResult)
    LoadImage.assert_called_once_with(str(Exr.resolve()), check_existing=True)


def test_headless_render_aliases_are_quarantined_before_scene_or_output_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    GetScene = MagicMock(side_effect=AssertionError("scene should not be accessed"))
    monkeypatch.setattr(HeadlessHandlerModule, "BPY_AVAILABLE", True)
    monkeypatch.setattr(
        HeadlessHandlerModule,
        "bpy",
        SimpleNamespace(data=SimpleNamespace(scenes=SimpleNamespace(get=GetScene))),
    )

    HandlerResult = HeadlessHandlerModule.manage_headless_mode(
        action=HeadlessModeAction.RENDER_HEADLESS.value,
        scene_name="Scene",
        output_path="outside.png",
    )
    CoreResult = HeadlessCoreModule.HeadlessModeManager.render_headless(object(), "outside.png", 1)
    Blender50Result = Blender50FeaturesModule.HeadlessModeManager.render_headless(
        object(), "outside.png", 1
    )

    assert ErrorCode(HandlerResult) == "OUTPUT_FAMILY_DISABLED"
    assert CoreResult["code"] == "OUTPUT_FAMILY_DISABLED"
    assert Blender50Result["code"] == "OUTPUT_FAMILY_DISABLED"
    GetScene.assert_not_called()


def test_mocap_bvh_denies_outside_root_before_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ReadRoot = tmp_path / "mocap"
    ReadRoot.mkdir()
    Outside = tmp_path / "outside.bvh"
    Outside.write_text("HIERARCHY", encoding="utf-8")
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot)
    ImportBvh = MagicMock(side_effect=AssertionError("BVH importer should not run"))
    monkeypatch.setattr(
        MocapModule,
        "bpy",
        SimpleNamespace(ops=SimpleNamespace(import_anim=SimpleNamespace(bvh=ImportBvh))),
    )

    Result = MocapModule._import_bvh({"filepath": str(Outside)})

    assert ErrorCode(Result) == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    ImportBvh.assert_not_called()


def test_mocap_bvh_preserves_authorized_single_file_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Bvh = tmp_path / "walk.bvh"
    Bvh.write_text("HIERARCHY", encoding="utf-8")
    ConfigureFilesystemPolicy(ReadRoot=tmp_path)
    ImportBvh = MagicMock()
    monkeypatch.setattr(
        MocapModule,
        "bpy",
        SimpleNamespace(
            ops=SimpleNamespace(import_anim=SimpleNamespace(bvh=ImportBvh)),
            context=SimpleNamespace(active_object=SimpleNamespace(name="WalkRig")),
        ),
    )

    Result = MocapModule._import_bvh({"filepath": str(Bvh)})

    assert Result["success"] is True
    ImportBvh.assert_called_once_with(
        filepath=str(Bvh.resolve()),
        global_scale=1.0,
        use_fps_scale=True,
        update_scene_fps=True,
        update_scene_duration=True,
    )


def test_mocap_fbx_input_family_is_quarantined_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ImportFbx = MagicMock(side_effect=AssertionError("FBX importer should not run"))
    monkeypatch.setattr(
        MocapModule,
        "bpy",
        SimpleNamespace(ops=SimpleNamespace(import_scene=SimpleNamespace(fbx=ImportFbx))),
    )

    Result = MocapModule._import_fbx({"filepath": "linked-animation.fbx"})

    assert ErrorCode(Result) == "INPUT_FAMILY_DISABLED"
    ImportFbx.assert_not_called()


def test_render_execution_is_quarantined_before_scene_or_process_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    GetScene = MagicMock(side_effect=AssertionError("scene should not be accessed"))
    monkeypatch.setattr(RenderingModule.ContextManagerV3, "get_scene", GetScene)

    FrameResult = RenderingModule._handle_render_frame(filepath="frame.png")
    AnimationResult = RenderingModule._handle_render_animation(filepath="animation.mp4")
    SubmitResult = RenderingModule._submit_async_render(None, {}, is_animation=True)

    assert ErrorCode(FrameResult) == "PROCESS_EXECUTION_DISABLED"
    assert ErrorCode(AnimationResult) == "PROCESS_EXECUTION_DISABLED"
    assert ErrorCode(SubmitResult) == "PROCESS_EXECUTION_DISABLED"
    GetScene.assert_not_called()
    assert not hasattr(RenderingModule, "AsyncJobManager")


def test_viewport_capture_denies_output_before_scene_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WriteRoot = tmp_path / "captures"
    WriteRoot.mkdir()
    ConfigureFilesystemPolicy(WriteRoot=WriteRoot)
    GetScene = MagicMock(side_effect=AssertionError("scene should not be accessed"))
    monkeypatch.setattr(RenderingModule.ContextManagerV3, "get_scene", GetScene)
    monkeypatch.setattr(RenderingModule.bpy.app, "background", False)

    OutsideResult = RenderingModule.get_viewport_screenshot(
        action="get_viewport_screenshot",
        filepath=str(tmp_path / "outside.png"),
    )
    MultiResult = RenderingModule.get_viewport_screenshot(
        action="get_viewport_screenshot",
        angles=["FRONT", "TOP"],
    )
    Base64Result = RenderingModule.get_viewport_screenshot_base64(
        action="get_viewport_screenshot_base64",
        filepath=str(tmp_path / "outside.png"),
    )
    MultiBase64Result = RenderingModule.get_viewport_screenshot_base64(
        action="get_viewport_screenshot_base64",
        views=["FRONT", "TOP"],
    )

    assert ErrorCode(OutsideResult) == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    assert ErrorCode(MultiResult) == "OUTPUT_FAMILY_DISABLED"
    assert ErrorCode(Base64Result) == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    assert ErrorCode(MultiBase64Result) == "OUTPUT_FAMILY_DISABLED"
    GetScene.assert_not_called()


def test_viewport_capture_default_is_inside_write_root(tmp_path: Path) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)

    CapturePath = RenderingModule._CapturePath(None, "PNG", "viewport")

    assert Path(CapturePath).parent == (tmp_path / "captures").resolve()
    assert Path(CapturePath).suffix == ".png"


def test_viewport_sink_reauthorizes_final_path_before_operator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WriteRoot = tmp_path / "captures"
    WriteRoot.mkdir()
    ConfigureFilesystemPolicy(WriteRoot=WriteRoot)
    Render = MagicMock(side_effect=AssertionError("capture operator should not run"))
    monkeypatch.setattr(
        RenderingModule,
        "safe_ops",
        SimpleNamespace(render=SimpleNamespace(opengl=Render)),
    )
    Scene = SimpleNamespace(render=SimpleNamespace(filepath="unchanged"))

    Result = RenderingModule._do_opengl_capture(Scene, str(tmp_path / "outside.png"))

    assert Result is False
    assert Scene.render.filepath == "unchanged"
    Render.assert_not_called()


def test_filesystem_routes_declare_capabilities() -> None:
    assert HANDLER_METADATA["manage_scene"]["capabilities"][SceneAction.OPEN_FILE.value] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
    ]
    assert HANDLER_METADATA["manage_scene"]["capabilities"][SceneAction.SAVE_FILE.value] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
    ]
    assert HANDLER_METADATA["manage_advanced_batch"]["capabilities"][
        AdvancedBatchAction.EXPORT_BATCH_VARIANTS.value
    ] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
    ]
    assert HANDLER_METADATA["manage_export"]["capabilities"][ExportAction.EXPORT_GLTF.value] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
    ]
    assert HANDLER_METADATA["manage_export_pipeline"]["capabilities"][
        ExportPipelineAction.EXPORT_GLTF.value
    ] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
    ]
    assert HANDLER_METADATA["manage_uvs"]["capabilities"][UVsAction.EXPORT_LAYOUT.value] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
    ]
    assert HANDLER_METADATA["manage_cloud_render"]["capabilities"][
        CloudRenderAction.PACKAGE_ASSETS.value
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]
    assert HANDLER_METADATA["manage_sequencer"]["capabilities"][
        SequencerAction.ADD_MOVIE.value
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_READ.value]
    assert HANDLER_METADATA["manage_sequencer"]["capabilities"][
        SequencerAction.RENDER_PREVIEW.value
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]
    assert HANDLER_METADATA["manage_light"]["capabilities"][LightAction.SETUP_HDRI.value] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
    ]
    assert HANDLER_METADATA["manage_headless_mode"]["capabilities"][
        HeadlessModeAction.RENDER_HEADLESS.value
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]
    assert HANDLER_METADATA["manage_mocap"]["capabilities"][MocapAction.IMPORT_BVH.value] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_READ.value,
    ]
    assert HANDLER_METADATA["manage_mocap"]["capabilities"][
        MocapAction.IMPORT_FBX_ANIMATION.value
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_READ.value]
    assert HANDLER_METADATA["manage_rendering"]["capabilities"][
        RenderAction.RENDER_FRAME.value
    ] == [
        Capability.MUTATE.value,
        Capability.FILESYSTEM_WRITE.value,
        Capability.PROCESS.value,
    ]
    assert HANDLER_METADATA["get_viewport_screenshot"]["capabilities"][
        "get_viewport_screenshot"
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]
    for ToolName in ("export_unity_fbx", "export_unity_collection", "export_lod_chain"):
        assert HANDLER_METADATA[ToolName]["capabilities"][ToolName] == [
            Capability.MUTATE.value,
            Capability.FILESYSTEM_WRITE.value,
        ]


def test_export_capabilities_defer_conditional_image_reads_to_path_policy(
    tmp_path: Path,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    ExportActions = (
        ("manage_export", ExportAction.EXPORT_GLTF.value),
        ("manage_export_pipeline", ExportPipelineAction.EXPORT_GLTF.value),
        ("manage_export_pipeline", ExportPipelineAction.EXPORT_ALL_FORMATS.value),
        ("manage_export_pipeline", ExportPipelineAction.EXPORT_GAMEDEV_READY.value),
        ("manage_advanced_batch", AdvancedBatchAction.EXPORT_BATCH_VARIANTS.value),
    )

    for ToolName, ActionName in ExportActions:
        Capabilities = HANDLER_METADATA[ToolName]["capabilities"][ActionName]
        assert Capability.FILESYSTEM_READ.value not in Capabilities
        assert SecurityManager.validate_action(ToolName, ActionName, Capabilities)


def test_dispatcher_denies_missing_root_before_file_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Handler = MagicMock(return_value={"success": True})
    monkeypatch.setitem(HANDLER_REGISTRY, "manage_scene", Handler)
    monkeypatch.setattr(SecurityManager, "is_safe_mode", staticmethod(lambda: False))

    Denied = dispatch_command(
        {
            "tool": "manage_scene",
            "params": {"action": SceneAction.OPEN_FILE.value, "filepath": "scene.blend"},
        },
        use_thread_safety=False,
    )
    assert Denied["code"] == "CAPABILITY_DENIED"
    Handler.assert_not_called()

    ConfigureFilesystemPolicy(ReadRoot=tmp_path)
    Allowed = dispatch_command(
        {
            "tool": "manage_scene",
            "params": {"action": SceneAction.OPEN_FILE.value, "filepath": "scene.blend"},
        },
        use_thread_safety=False,
    )
    assert Allowed["success"] is True
    assert Allowed["_meta"]["tool"] == "manage_scene"
    Handler.assert_called_once()
