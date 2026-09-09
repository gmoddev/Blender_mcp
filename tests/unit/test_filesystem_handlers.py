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
    SceneAction,
    SequencerAction,
    UVsAction,
)
from blender_mcp.core.export_pipeline import ExportValidator, GLTFExporter, USDExporter
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
from blender_mcp.handlers import manage_scene as SceneModule
from blender_mcp.handlers import manage_sequencer as SequencerModule
from blender_mcp.handlers import manage_uvs as UVModule
from blender_mcp.handlers import unity_export as UnityModule
import blender_mcp.core.export_pipeline as ExportCoreModule


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
    monkeypatch.setattr(SceneModule, "safe_ops", SimpleNamespace(wm=SimpleNamespace(open_mainfile=OpenMainFile)))
    monkeypatch.setattr(SceneModule, "execute_on_main_thread", lambda Function, **_Kwargs: Function())

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
    monkeypatch.setattr(SceneModule, "safe_ops", SimpleNamespace(wm=SimpleNamespace(save_mainfile=SaveMainFile)))
    monkeypatch.setattr(SceneModule, "execute_on_main_thread", lambda Function, **_Kwargs: Function())

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


def test_usd_disables_texture_sidecars_and_path_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    Export = MagicMock()
    monkeypatch.setattr(ExportCoreModule.SafeOperators, "export_usd", Export)
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

    assert Result["success"] is True
    assert PathResult["code"] == "EXPORT_ERROR"
    assert Export.call_count == 1
    assert Export.call_args.kwargs["export_textures"] is False
    assert Export.call_args.kwargs["overwrite_textures"] is False


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
    monkeypatch.setattr(UVModule, "safe_ops", SimpleNamespace(uv=SimpleNamespace(export_layout=UVExport)))
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
    ] == [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]
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
    for ToolName in ("export_unity_fbx", "export_unity_collection", "export_lod_chain"):
        assert HANDLER_METADATA[ToolName]["capabilities"][ToolName] == [
            Capability.MUTATE.value,
            Capability.FILESYSTEM_WRITE.value,
        ]


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
