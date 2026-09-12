"""Scene serialization regressions for legacy provider credentials."""

from __future__ import annotations

from pathlib import Path

from blender_mcp import (
    HasLegacySceneCredentials,
    LegacySceneCredentialProperties,
    PurgeLegacySceneCredentials,
)


def test_registered_scene_source_contains_no_provider_secret_properties() -> None:
    Source = (Path(__file__).parents[2] / "blender_mcp" / "__init__.py").read_text(encoding="utf-8")
    RegistrationBlock = Source.split("properties_to_add = [", 1)[1].split("]", 1)[0]

    for Name in LegacySceneCredentialProperties:
        assert Name not in RegistrationBlock


def test_legacy_detection_inspects_names_without_reading_values() -> None:
    class Scene(dict):
        def __getitem__(self, Key):
            raise AssertionError("legacy credential value was read")

    SceneValue = Scene({LegacySceneCredentialProperties[0]: "do-not-read"})
    assert HasLegacySceneCredentials(SceneValue) is True


def test_explicit_legacy_cleanup_removes_all_scenes_without_copying_values() -> None:
    Scenes = [
        {LegacySceneCredentialProperties[0]: "first", "ordinary": 1},
        {
            LegacySceneCredentialProperties[2]: "second",
            LegacySceneCredentialProperties[3]: "third",
        },
    ]

    assert PurgeLegacySceneCredentials(Scenes) == 3
    assert Scenes == [{"ordinary": 1}, {}]
