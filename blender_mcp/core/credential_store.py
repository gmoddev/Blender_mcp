"""Fail-closed access to OS-backed, user-scoped credentials.

Credential values are deliberately absent from exceptions, status objects, and logs.  Callers
must use a named slot so untrusted repository or protocol text cannot choose arbitrary keyring
accounts.
"""

from __future__ import annotations

from enum import Enum
from threading import RLock
from typing import Any


ServiceName = "gmoddev.BlenderMCP"
MaxCredentialLength = 65_536


class CredentialName(str, Enum):
    CONTROL_AUTH_TOKEN = "control/auth-token"
    SKETCHFAB_API_KEY = "provider/sketchfab/api-key"
    HYPER3D_API_KEY = "provider/hyper3d/api-key"
    HUNYUAN_SECRET_ID = "provider/hunyuan/secret-id"
    HUNYUAN_SECRET_KEY = "provider/hunyuan/secret-key"


class CredentialStoreError(RuntimeError):
    """A redacted credential-boundary failure safe to return to local callers."""

    def __init__(self, Code: str, Message: str) -> None:
        super().__init__(Message)
        self.Code = Code
        self.PublicMessage = Message


AllowedBackendTypes = frozenset(
    {
        "keyring.backends.Windows.WinVaultKeyring",
        "keyring.backends.macOS.Keyring",
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.libsecret.Keyring",
        "keyring.backends.kwallet.DBusKeyring",
        "keyring.backends.kwallet.DBusKeyringKWallet4",
    }
)


def _GetBackendType(Backend: Any) -> str:
    BackendType = type(Backend)
    return f"{BackendType.__module__}.{BackendType.__qualname__}"


def _IsAllowedBackend(Backend: Any) -> bool:
    try:
        return _GetBackendType(Backend) in AllowedBackendTypes and float(Backend.priority) > 0
    except (AttributeError, TypeError, ValueError):
        return False


def _ResolveBackend() -> Any:
    try:
        import keyring

        Backend = keyring.get_keyring()
        if _IsAllowedBackend(Backend):
            return Backend

        # A chainer may include opt-in third-party or plaintext implementations. Select only a
        # core OS backend instead of trusting the chain as a whole.
        Candidates = getattr(Backend, "backends", ())
        AllowedCandidates = [Candidate for Candidate in Candidates if _IsAllowedBackend(Candidate)]
        if AllowedCandidates:
            return max(AllowedCandidates, key=lambda Candidate: float(Candidate.priority))
    except Exception:
        pass
    raise CredentialStoreError(
        "CREDENTIAL_BACKEND_UNAVAILABLE",
        "A supported OS credential store is unavailable",
    )


class CredentialStore:
    """Store fixed-purpose credentials in one validated OS keyring backend."""

    def __init__(self, Backend: Any | None = None) -> None:
        ResolvedBackend = Backend if Backend is not None else _ResolveBackend()
        if not _IsAllowedBackend(ResolvedBackend):
            raise CredentialStoreError(
                "CREDENTIAL_BACKEND_UNAVAILABLE",
                "A supported OS credential store is unavailable",
            )
        self._Backend = ResolvedBackend
        self._Lock = RLock()

    def GetCredential(self, Name: CredentialName) -> str | None:
        try:
            with self._Lock:
                Value = self._Backend.get_password(ServiceName, Name.value)
        except Exception:
            raise CredentialStoreError(
                "CREDENTIAL_READ_FAILED",
                "The OS credential store could not read the credential",
            ) from None
        if Value is None:
            return None
        if not isinstance(Value, str) or not Value or len(Value) > MaxCredentialLength:
            raise CredentialStoreError(
                "CREDENTIAL_VALUE_INVALID",
                "The stored credential is invalid",
            )
        return Value

    def SetCredential(self, Name: CredentialName, Value: str) -> None:
        if not isinstance(Value, str) or not Value or len(Value) > MaxCredentialLength:
            raise CredentialStoreError(
                "CREDENTIAL_VALUE_INVALID",
                "Credential values must be non-empty bounded strings",
            )
        try:
            with self._Lock:
                self._Backend.set_password(ServiceName, Name.value, Value)
        except Exception:
            raise CredentialStoreError(
                "CREDENTIAL_WRITE_FAILED",
                "The OS credential store could not save the credential",
            ) from None

    def DeleteCredential(self, Name: CredentialName) -> bool:
        if self.GetCredential(Name) is None:
            return False
        try:
            with self._Lock:
                self._Backend.delete_password(ServiceName, Name.value)
        except Exception:
            raise CredentialStoreError(
                "CREDENTIAL_DELETE_FAILED",
                "The OS credential store could not delete the credential",
            ) from None
        return True


def GetSystemCredential(Name: CredentialName) -> str | None:
    return CredentialStore().GetCredential(Name)


def SetSystemCredential(Name: CredentialName, Value: str) -> None:
    CredentialStore().SetCredential(Name, Value)


def DeleteSystemCredential(Name: CredentialName) -> bool:
    return CredentialStore().DeleteCredential(Name)
