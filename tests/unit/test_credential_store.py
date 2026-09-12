"""Security regression tests for the OS credential boundary."""

from __future__ import annotations

import pytest

from blender_mcp.core.credential_store import (
    CredentialName,
    CredentialStore,
    CredentialStoreError,
    MaxCredentialLength,
    ServiceName,
)


class WinVaultKeyring:
    __module__ = "keyring.backends.Windows"
    priority = 5

    def __init__(self) -> None:
        self.Values: dict[tuple[str, str], str] = {}
        self.Failure: Exception | None = None
        self.FailOperation: str | None = None

    def get_password(self, Service: str, Account: str) -> str | None:
        if self.Failure and self.FailOperation in (None, "read"):
            raise self.Failure
        return self.Values.get((Service, Account))

    def set_password(self, Service: str, Account: str, Value: str) -> None:
        if self.Failure and self.FailOperation in (None, "write"):
            raise self.Failure
        self.Values[(Service, Account)] = Value

    def delete_password(self, Service: str, Account: str) -> None:
        if self.Failure and self.FailOperation in (None, "delete"):
            raise self.Failure
        del self.Values[(Service, Account)]


def test_named_credential_round_trip_uses_fixed_service_and_account() -> None:
    Backend = WinVaultKeyring()
    Store = CredentialStore(Backend)

    Store.SetCredential(CredentialName.CONTROL_AUTH_TOKEN, "secret-value")

    assert Backend.Values == {(ServiceName, "control/auth-token"): "secret-value"}
    assert Store.GetCredential(CredentialName.CONTROL_AUTH_TOKEN) == "secret-value"
    assert Store.DeleteCredential(CredentialName.CONTROL_AUTH_TOKEN) is True
    assert Store.GetCredential(CredentialName.CONTROL_AUTH_TOKEN) is None
    assert Store.DeleteCredential(CredentialName.CONTROL_AUTH_TOKEN) is False


@pytest.mark.parametrize(
    "Value",
    ["", "x" * (MaxCredentialLength + 1)],
    ids=["empty", "oversized"],
)
def test_invalid_values_fail_before_backend_access(Value: str) -> None:
    Backend = WinVaultKeyring()
    Store = CredentialStore(Backend)

    with pytest.raises(CredentialStoreError) as Captured:
        Store.SetCredential(CredentialName.SKETCHFAB_API_KEY, Value)

    assert Captured.value.Code == "CREDENTIAL_VALUE_INVALID"
    assert Backend.Values == {}


def test_null_or_unapproved_backend_fails_closed() -> None:
    class Keyring:
        __module__ = "keyring.backends.null"
        priority = -1

    with pytest.raises(CredentialStoreError) as Captured:
        CredentialStore(Keyring())

    assert Captured.value.Code == "CREDENTIAL_BACKEND_UNAVAILABLE"


@pytest.mark.parametrize(
    ("Operation", "ExpectedCode"),
    [
        ("read", "CREDENTIAL_READ_FAILED"),
        ("write", "CREDENTIAL_WRITE_FAILED"),
        ("delete", "CREDENTIAL_DELETE_FAILED"),
    ],
)
def test_backend_errors_are_redacted(Operation: str, ExpectedCode: str) -> None:
    SensitiveText = "provider-secret-from-backend"
    Backend = WinVaultKeyring()
    if Operation == "delete":
        Backend.Values[(ServiceName, CredentialName.HUNYUAN_SECRET_KEY.value)] = "present"
    Backend.Failure = RuntimeError(SensitiveText)
    Backend.FailOperation = Operation
    Store = CredentialStore(Backend)

    with pytest.raises(CredentialStoreError) as Captured:
        if Operation == "read":
            Store.GetCredential(CredentialName.HUNYUAN_SECRET_KEY)
        elif Operation == "write":
            Store.SetCredential(CredentialName.HUNYUAN_SECRET_KEY, "new-value")
        else:
            Store.DeleteCredential(CredentialName.HUNYUAN_SECRET_KEY)

    assert Captured.value.Code == ExpectedCode
    assert SensitiveText not in str(Captured.value)
