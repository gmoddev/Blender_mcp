"""Unit tests for combined metarig world-space bounds."""

from types import SimpleNamespace

import pytest

from blender_mcp.handlers.manage_rigging import CalculateMetarigBounds


def Point(X: float, Y: float, Z: float) -> SimpleNamespace:
    return SimpleNamespace(x=X, y=Y, z=Z)


def test_single_mesh_bounds_use_extrema_midpoint() -> None:
    WorldBounds = [
        Point(X, Y, Z)
        for X in (-2.0, 4.0)
        for Y in (3.0, 7.0)
        for Z in (-1.0, 5.0)
    ]

    assert CalculateMetarigBounds(WorldBounds) == pytest.approx((1.0, 5.0, -1.0, 6.0))


def test_multiple_meshes_use_combined_extrema_instead_of_fixed_divisor() -> None:
    FirstMeshBounds = [
        Point(X, Y, Z)
        for X in (-4.0, -2.0)
        for Y in (1.0, 3.0)
        for Z in (0.0, 2.0)
    ]
    SecondMeshBounds = [
        Point(X, Y, Z)
        for X in (6.0, 10.0)
        for Y in (5.0, 9.0)
        for Z in (-2.0, 8.0)
    ]

    CombinedBounds = FirstMeshBounds + SecondMeshBounds

    assert CalculateMetarigBounds(CombinedBounds) == pytest.approx((3.0, 5.0, -2.0, 10.0))


def test_empty_bounds_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="At least one world-space bound point is required"):
        CalculateMetarigBounds([])
