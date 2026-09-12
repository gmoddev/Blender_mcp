"""Unit-suite policy fixtures.

Production defaults remain fail closed. Legacy handler tests exercise handler behavior rather than
authorization, so the unit harness explicitly opts into full/raw mode. Security-boundary tests
override these methods locally and continue to assert the production policy.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def PermissiveUnitSecurity(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Import after each test module has installed its Blender API doubles. Exercise
    # the production snapshot path instead of replacing its compatibility methods.
    from blender_mcp.core import credential_store
    from blender_mcp.core.security import ConfigureSecurityPolicy, ResetSecurityPolicy

    # Unit tests must never inspect or mutate the developer's real OS credential store.
    monkeypatch.setattr(credential_store, "GetSystemCredential", lambda Name: None)
    ConfigureSecurityPolicy(SafeMode=False, RawCodeEnabled=True)
    yield
    ResetSecurityPolicy()
