"""Unit-suite policy fixtures.

Production defaults remain fail closed. Legacy handler tests exercise handler behavior rather than
authorization, so the unit harness explicitly opts into full/raw mode. Security-boundary tests
override these methods locally and continue to assert the production policy.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def PermissiveUnitSecurity(monkeypatch: pytest.MonkeyPatch) -> None:
    # Import after each test module has installed its Blender API doubles.
    from blender_mcp.core.security import SecurityManager

    monkeypatch.setattr(SecurityManager, "is_safe_mode", staticmethod(lambda: False))
    monkeypatch.setattr(SecurityManager, "is_raw_code_enabled", staticmethod(lambda: True))
