"""Security coverage for bounded, regex-free Blender name selectors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from blender_mcp.core.enums import AdvancedBatchAction, BatchAction
from blender_mcp.core.name_selector import (
    MaxCandidateCount,
    MaxSelectorCount,
    MaxSelectorLength,
    NameSelectorError,
    ParseNameSelector,
    ValidateCandidateName,
    ValidateSelectorBudget,
)
from blender_mcp.handlers import manage_advanced_batch as AdvancedBatchModule
from blender_mcp.handlers import manage_batch as BatchModule


class ObjectCollection(list[object]):
    def get(self, Name: str) -> object | None:
        return next((Object for Object in self if getattr(Object, "name", None) == Name), None)


class SceneObject:
    def __init__(self, Name: str, ObjectType: str = "MESH"):
        self.name = Name
        self.type = ObjectType
        self.hide_viewport = False
        self.hide_render = False
        self.Selected = False
        self.data = SimpleNamespace(materials=[])

    def hide_get(self) -> bool:
        return False

    def visible_get(self) -> bool:
        return True

    def select_set(self, Value: bool) -> None:
        self.Selected = Value


class ExplodingBpy:
    @property
    def data(self) -> object:
        raise AssertionError("selector denial must occur before scene access")

    @property
    def context(self) -> object:
        raise AssertionError("selector denial must occur before scene access")


def ErrorCode(Result: dict[str, object]) -> str:
    return str(Result["errors"][0]["code"])  # type: ignore[index]


@pytest.mark.parametrize(
    ("RawSelector", "Matches", "Misses"),
    [
        ({"mode": "EXACT", "value": "Hero"}, "Hero", "Hero.001"),
        ({"mode": "PREFIX", "value": "Hero"}, "Hero.001", "RigHero"),
        ({"mode": "SUFFIX", "value": "_LOD0"}, "Hero_LOD0", "Hero_LOD1"),
        ({"mode": "GLOB", "value": "Hero_??"}, "Hero_01", "Hero_001"),
        ({"mode": "GLOB", "value": "Hero*LOD?"}, "Hero_Body_LOD0", "Rig_Body_LOD0"),
    ],
)
def test_modes_match_without_regular_expressions(
    RawSelector: dict[str, str], Matches: str, Misses: str
) -> None:
    Selector = ParseNameSelector(RawSelector)

    assert Selector.Matches(Matches) is True
    assert Selector.Matches(Misses) is False


@pytest.mark.parametrize(
    "RawSelector",
    [
        None,
        {},
        {"mode": "REGEX", "value": ".*"},
        {"mode": "EXACT", "value": ""},
        {"mode": "EXACT", "value": "bad\nname"},
        {"mode": "EXACT", "value": "Hero", "extra": True},
        {"mode": "EXACT", "value": "x" * (MaxSelectorLength + 1)},
        {"mode": "GLOB", "value": "*" * 17},
    ],
)
def test_invalid_or_overcomplex_selectors_fail_closed(RawSelector: object) -> None:
    with pytest.raises(NameSelectorError):
        ParseNameSelector(RawSelector)


def test_candidate_count_and_scene_name_are_bounded() -> None:
    ValidateSelectorBudget(MaxCandidateCount)
    with pytest.raises(NameSelectorError, match="candidate budget"):
        ValidateSelectorBudget(MaxCandidateCount + 1)
    with pytest.raises(NameSelectorError, match="candidate object name"):
        ValidateCandidateName("bad\nname")
    with pytest.raises(NameSelectorError, match="candidate budget"):
        ValidateSelectorBudget(0, MaxSelectorCount + 1)


def test_legacy_batch_regex_fails_before_scene_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BatchModule, "bpy", ExplodingBpy())

    Result = BatchModule.manage_batch(action=BatchAction.DELETE.value, pattern="(a+)+$")

    assert ErrorCode(Result) == "REGEX_SELECTOR_DISABLED"


def test_legacy_pipeline_regex_is_preflighted_before_prior_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AdvancedBatchModule, "bpy", ExplodingBpy())

    Result = AdvancedBatchModule.manage_advanced_batch(
        action=AdvancedBatchAction.PIPELINE_EXECUTE.value,
        pipeline={
            "steps": [
                {"type": "modify", "params": {"location_offset": [1, 0, 0]}},
                {"type": "select", "params": {"pattern": "(a+)+$"}},
            ]
        },
    )

    assert ErrorCode(Result) == "REGEX_SELECTOR_DISABLED"


def test_legacy_advanced_filter_regex_fails_before_scene_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AdvancedBatchModule, "bpy", ExplodingBpy())

    Result = AdvancedBatchModule.manage_advanced_batch(
        action=AdvancedBatchAction.FILTER_AND_PROCESS.value,
        filter={"name_pattern": "(a+)+$"},
    )

    assert ErrorCode(Result) == "REGEX_SELECTOR_DISABLED"


def test_batch_selector_targets_only_matching_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    Hero = SceneObject("Hero_Body")
    Rig = SceneObject("Rig_Body")
    Objects = ObjectCollection([Hero, Rig])
    monkeypatch.setattr(
        BatchModule,
        "bpy",
        SimpleNamespace(
            data=SimpleNamespace(objects=Objects), context=SimpleNamespace(selected_objects=[])
        ),
    )

    Result = BatchModule.manage_batch(
        action=BatchAction.SET_VISIBILITY.value,
        selector={"mode": "PREFIX", "value": "Hero_"},
        visibility=False,
    )

    assert Result["success"] is True
    assert Hero.hide_viewport is True
    assert Hero.hide_render is True
    assert Rig.hide_viewport is False


def test_select_by_name_uses_bounded_glob(monkeypatch: pytest.MonkeyPatch) -> None:
    Hero = SceneObject("Hero_Body")
    Rig = SceneObject("Rig_Body")
    Objects = ObjectCollection([Hero, Rig])
    monkeypatch.setattr(
        BatchModule,
        "bpy",
        SimpleNamespace(
            data=SimpleNamespace(objects=Objects), context=SimpleNamespace(selected_objects=[])
        ),
    )
    monkeypatch.setattr(BatchModule.ContextManagerV3, "deselect_all_objects", MagicMock())

    Result = BatchModule.manage_batch(
        action=BatchAction.SELECT_BY_NAME.value,
        selector={"mode": "GLOB", "value": "Hero_*"},
    )

    assert Result["success"] is True
    assert Hero.Selected is True
    assert Rig.Selected is False


def test_advanced_pipeline_and_filter_use_shared_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Objects = ObjectCollection([SceneObject("Hero_Body"), SceneObject("Rig_Body")])
    monkeypatch.setattr(
        AdvancedBatchModule,
        "bpy",
        SimpleNamespace(data=SimpleNamespace(objects=Objects)),
    )

    PipelineResult = AdvancedBatchModule.manage_advanced_batch(
        action=AdvancedBatchAction.PIPELINE_EXECUTE.value,
        pipeline={
            "steps": [
                {
                    "type": "select",
                    "params": {"selector": {"mode": "SUFFIX", "value": "_Body"}},
                }
            ]
        },
    )
    FilterResult = AdvancedBatchModule.manage_advanced_batch(
        action=AdvancedBatchAction.FILTER_AND_PROCESS.value,
        filter={"name_selector": {"mode": "PREFIX", "value": "Hero_"}},
        operations=[{"type": "inspect"}],
    )

    assert PipelineResult["data"]["final_context"]["selected_count"] == 2
    assert FilterResult["data"]["filtered_objects"] == ["Hero_Body"]


def test_handlers_contain_no_regex_execution() -> None:
    RepoRoot = Path(__file__).resolve().parents[2]
    for RelativePath in (
        "blender_mcp/handlers/manage_batch.py",
        "blender_mcp/handlers/manage_advanced_batch.py",
    ):
        Source = (RepoRoot / RelativePath).read_text(encoding="utf-8")
        assert "\nimport re\n" not in Source
        assert "\nfrom re " not in Source
        assert "re.compile" not in Source
        assert "re.match" not in Source
        assert "re.search" not in Source
