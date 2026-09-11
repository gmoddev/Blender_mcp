"""Authenticated, versioned connection sessions for the local MCP transport."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import secrets
import socket
import struct
import time
import uuid
from enum import Enum
from typing import Any, Dict, Iterable, cast

from .protocol import ProtocolError, recv_message, send_message

PROTOCOL_VERSION = 2
AUTH_TOKEN_ENV = "BLENDER_MCP_AUTH_TOKEN"
AUTH_TOKEN_BYTES = 32
MIN_AUTH_TOKEN_LENGTH = 43
MAX_IDENTIFIER_LENGTH = 128
HANDSHAKE_TIMEOUT_SECONDS = 5.0
PREAUTH_MAX_FRAME_BYTES = 8 * 1024
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
ENVELOPE_FIELDS = {
    "protocol_version",
    "message_type",
    "request_id",
    "session_id",
    "payload",
}


class MessageType(str, Enum):
    CHALLENGE = "CHALLENGE"
    AUTH = "AUTH"
    AUTH_OK = "AUTH_OK"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    ERROR = "ERROR"


class SessionState(str, Enum):
    CHALLENGE_SENT = "CHALLENGE_SENT"
    AUTHENTICATED = "AUTHENTICATED"
    CLOSED = "CLOSED"


class SessionError(ProtocolError):
    """A bounded authentication, session, or correlation failure."""

    def __init__(
        self,
        Code: str,
        Message: str,
        RequestId: str = "protocol",
        SessionId: str = "",
    ) -> None:
        super().__init__(Code, Message)
        self.RequestId = RequestId
        self.SessionId = SessionId


def ValidateAuthToken(AuthToken: str) -> str:
    """Require the canonical format emitted by ``secrets.token_urlsafe(32)``."""
    if not isinstance(AuthToken, str):
        raise SessionError(
            "AUTH_CONFIGURATION_ERROR",
            "Authentication credential must be a generated 256-bit base64url token",
        )
    Token = AuthToken.strip()
    if len(Token) != MIN_AUTH_TOKEN_LENGTH or not re.fullmatch(r"[A-Za-z0-9_-]{43}", Token):
        raise SessionError(
            "AUTH_CONFIGURATION_ERROR",
            "Authentication credential must be a generated 256-bit base64url token",
        )
    try:
        TokenBytes = base64.urlsafe_b64decode(Token + "=")
    except (binascii.Error, ValueError) as Error:
        raise SessionError(
            "AUTH_CONFIGURATION_ERROR",
            "Authentication credential must be a generated 256-bit base64url token",
        ) from Error
    Canonical = base64.urlsafe_b64encode(TokenBytes).decode("ascii").rstrip("=")
    if (
        len(TokenBytes) != AUTH_TOKEN_BYTES
        or not secrets.compare_digest(Token, Canonical)
        or len(set(TokenBytes)) < 16
    ):
        raise SessionError(
            "AUTH_CONFIGURATION_ERROR",
            "Authentication credential must be a generated 256-bit base64url token",
        )
    return Token


def BuildEnvelope(
    MessageKind: MessageType | str,
    RequestId: str,
    SessionId: str,
    Payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the sole version-one wire envelope."""
    Kind = MessageKind.value if isinstance(MessageKind, MessageType) else str(MessageKind)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": Kind,
        "request_id": RequestId,
        "session_id": SessionId,
        "payload": Payload,
    }


def BuildErrorEnvelope(
    Code: str,
    Message: str,
    RequestId: str = "protocol",
    SessionId: str = "",
) -> Dict[str, Any]:
    """Build a bounded error envelope without reflecting caller input."""
    return BuildEnvelope(
        MessageType.ERROR,
        RequestId,
        SessionId,
        {"error": {"code": Code, "message": Message}},
    )


def NormalizeRequestId(Value: Any) -> str:
    """Create a stable, log-safe wire identity from a JSON-RPC identifier."""
    Candidate = str(Value)
    if REQUEST_ID_PATTERN.fullmatch(Candidate):
        return Candidate
    try:
        Canonical = json.dumps(Value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        Canonical = repr(type(Value))
    Digest = hashlib.sha256(Canonical.encode("utf-8", errors="replace")).hexdigest()
    return f"jsonrpc-{Digest}"


def NormalizeJsonRpcRequestId(Value: Any) -> str:
    """Encode a JSON-RPC identifier without collapsing distinct JSON value types."""
    if isinstance(Value, str):
        TypeTag = "string"
    elif isinstance(Value, bool):
        TypeTag = "boolean"
    elif Value is None:
        TypeTag = "null"
    elif isinstance(Value, (int, float)):
        TypeTag = "number"
    else:
        TypeTag = "invalid"
    try:
        Canonical = json.dumps(Value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        Canonical = repr(type(Value))
    Digest = hashlib.sha256(Canonical.encode("utf-8", errors="replace")).hexdigest()
    return f"jsonrpc-{TypeTag}-{Digest}"


def BuildScopedRequestId(ClientInstanceId: str, RequestId: str) -> str:
    """Build a bounded ledger key from authenticated client and wire identities."""
    if not isinstance(ClientInstanceId, str) or not REQUEST_ID_PATTERN.fullmatch(ClientInstanceId):
        raise SessionError("INVALID_CLIENT_INSTANCE", "Authenticated client identity is invalid")
    if not isinstance(RequestId, str) or not REQUEST_ID_PATTERN.fullmatch(RequestId):
        raise SessionError("INVALID_REQUEST_ID", "A bounded request_id is required")
    Canonical = b"".join(
        struct.pack(">I", len(Part)) + Part
        for Part in (ClientInstanceId.encode("utf-8"), RequestId.encode("utf-8"))
    )
    return f"scoped-{hashlib.sha256(Canonical).hexdigest()}"


def ValidateEnvelope(Envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the common envelope and return it unchanged."""
    if not isinstance(Envelope, dict):
        raise SessionError("INVALID_ENVELOPE", "Envelope must be an object")
    if set(Envelope) != ENVELOPE_FIELDS:
        raise SessionError(
            "INVALID_ENVELOPE",
            f"Envelope fields do not match protocol version {PROTOCOL_VERSION}",
        )

    RequestId = Envelope.get("request_id")
    SessionId = Envelope.get("session_id")
    if not isinstance(RequestId, str) or not REQUEST_ID_PATTERN.fullmatch(RequestId):
        raise SessionError("INVALID_REQUEST_ID", "A bounded request_id is required")
    if (
        not isinstance(SessionId, str)
        or len(SessionId) > MAX_IDENTIFIER_LENGTH
        or (SessionId and not REQUEST_ID_PATTERN.fullmatch(SessionId))
    ):
        raise SessionError("INVALID_SESSION", "session_id is invalid", RequestId)
    if Envelope.get("protocol_version") != PROTOCOL_VERSION:
        raise SessionError(
            "UNSUPPORTED_PROTOCOL",
            f"Only protocol version {PROTOCOL_VERSION} is supported",
            RequestId,
            SessionId,
        )
    MessageKind = Envelope.get("message_type")
    if MessageKind not in {Item.value for Item in MessageType}:
        raise SessionError("INVALID_MESSAGE_TYPE", "Unknown message_type", RequestId, SessionId)
    if not isinstance(Envelope.get("payload"), dict):
        raise SessionError(
            "INVALID_PAYLOAD", "Envelope payload must be an object", RequestId, SessionId
        )
    return Envelope


def CreateProof(AuthToken: str, Direction: str, Fields: Iterable[str | int]) -> str:
    """Create a domain-separated HMAC over length-delimited canonical fields."""
    Token = ValidateAuthToken(AuthToken).encode("utf-8")
    Parts = [b"BLENDER_MCP_AUTH_V2", Direction.encode("ascii")]
    Parts.extend(str(Field).encode("utf-8") for Field in Fields)
    Canonical = b"".join(struct.pack(">I", len(Part)) + Part for Part in Parts)
    return hmac.new(Token, Canonical, hashlib.sha256).hexdigest()


class ServerSession:
    """Per-connection server-side handshake and correlation state."""

    def __init__(
        self,
        AuthToken: str,
        InstanceId: str,
        AuthEpoch: int,
        LifetimeSeconds: float = HANDSHAKE_TIMEOUT_SECONDS,
    ) -> None:
        self.AuthToken = ValidateAuthToken(AuthToken)
        self.InstanceId = InstanceId
        self.AuthEpoch = AuthEpoch
        self.SessionId = str(uuid.uuid4())
        self.RequestId = str(uuid.uuid4())
        self.ServerNonce = secrets.token_urlsafe(32)
        self.ExpiresAt = time.time() + LifetimeSeconds
        self.ClientNonce = ""
        self.ClientInstanceId = ""
        self.State = SessionState.CHALLENGE_SENT

    def BuildChallenge(self) -> Dict[str, Any]:
        return BuildEnvelope(
            MessageType.CHALLENGE,
            self.RequestId,
            self.SessionId,
            {
                "instance_id": self.InstanceId,
                "server_nonce": self.ServerNonce,
                "auth_epoch": self.AuthEpoch,
                "expires_at": self.ExpiresAt,
                "algorithm": "HMAC-SHA256",
            },
        )

    def Authenticate(self, Envelope: Dict[str, Any]) -> Dict[str, Any]:
        Validated = ValidateEnvelope(Envelope)
        if self.State != SessionState.CHALLENGE_SENT or time.time() > self.ExpiresAt:
            raise SessionError(
                "AUTH_EXPIRED", "Authentication challenge expired", self.RequestId, self.SessionId
            )
        if Validated["message_type"] != MessageType.AUTH.value:
            raise SessionError(
                "AUTH_REQUIRED", "Authentication is required", self.RequestId, self.SessionId
            )
        if Validated["request_id"] != self.RequestId or Validated["session_id"] != self.SessionId:
            raise SessionError(
                "AUTH_SESSION_MISMATCH",
                "Authentication is not bound to this challenge",
                self.RequestId,
                self.SessionId,
            )

        Payload = Validated["payload"]
        if set(Payload) != {"client_instance_id", "client_nonce", "proof"}:
            raise SessionError(
                "AUTH_FAILED", "Authentication proof is invalid", self.RequestId, self.SessionId
            )
        ClientInstanceId = Payload.get("client_instance_id")
        ClientNonce = Payload.get("client_nonce")
        SuppliedProof = Payload.get("proof")
        if (
            not isinstance(ClientInstanceId, str)
            or not REQUEST_ID_PATTERN.fullmatch(ClientInstanceId)
            or not isinstance(ClientNonce, str)
            or not 32 <= len(ClientNonce) <= MAX_IDENTIFIER_LENGTH
            or not isinstance(SuppliedProof, str)
            or len(SuppliedProof) != 64
        ):
            raise SessionError(
                "AUTH_FAILED", "Authentication proof is invalid", self.RequestId, self.SessionId
            )

        Fields = self._ProofFields(ClientInstanceId, ClientNonce)
        ExpectedProof = CreateProof(self.AuthToken, "CLIENT", Fields)
        if not secrets.compare_digest(SuppliedProof, ExpectedProof):
            raise SessionError(
                "AUTH_FAILED", "Authentication proof is invalid", self.RequestId, self.SessionId
            )

        self.ClientNonce = ClientNonce
        self.ClientInstanceId = ClientInstanceId
        self.State = SessionState.AUTHENTICATED
        ServerProof = CreateProof(self.AuthToken, "SERVER", Fields)
        return BuildEnvelope(
            MessageType.AUTH_OK,
            self.RequestId,
            self.SessionId,
            {"instance_id": self.InstanceId, "auth_epoch": self.AuthEpoch, "proof": ServerProof},
        )

    def ValidateRequest(self, Envelope: Dict[str, Any]) -> Dict[str, Any]:
        Validated = ValidateEnvelope(Envelope)
        if self.State != SessionState.AUTHENTICATED:
            raise SessionError(
                "AUTH_REQUIRED", "Authentication is required", self.RequestId, self.SessionId
            )
        if Validated["message_type"] != MessageType.REQUEST.value:
            raise SessionError(
                "INVALID_MESSAGE_TYPE",
                "Expected a request envelope",
                Validated["request_id"],
                self.SessionId,
            )
        if Validated["session_id"] != self.SessionId:
            raise SessionError(
                "SESSION_MISMATCH",
                "Request belongs to another session",
                Validated["request_id"],
                self.SessionId,
            )
        return cast(Dict[str, Any], Validated["payload"])

    def Close(self) -> None:
        self.State = SessionState.CLOSED

    def _ProofFields(self, ClientInstanceId: str, ClientNonce: str) -> list[str | int]:
        return [
            PROTOCOL_VERSION,
            self.InstanceId,
            self.AuthEpoch,
            self.SessionId,
            self.RequestId,
            self.ServerNonce,
            ClientInstanceId,
            ClientNonce,
        ]


class ClientSession:
    """Bridge-side authenticated connection state."""

    def __init__(self, AuthToken: str, ClientInstanceId: str | None = None) -> None:
        self.AuthToken = ValidateAuthToken(AuthToken)
        self.ClientInstanceId = NormalizeRequestId(ClientInstanceId or str(uuid.uuid4()))
        self.InstanceId = ""
        self.AuthEpoch = 0
        self.SessionId = ""
        self.State = SessionState.CLOSED

    def PerformHandshake(self, Sock: socket.socket) -> None:
        ChallengeRaw = recv_message(
            Sock,
            max_frame_bytes=PREAUTH_MAX_FRAME_BYTES,
            header_timeout=HANDSHAKE_TIMEOUT_SECONDS,
            body_timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
        if ChallengeRaw is None:
            raise SessionError("AUTH_REQUIRED", "Server closed before authentication")
        Challenge = ValidateEnvelope(ChallengeRaw)
        if Challenge["message_type"] == MessageType.ERROR.value:
            self._RaiseRemoteError(Challenge)
        if Challenge["message_type"] != MessageType.CHALLENGE.value:
            raise SessionError("AUTH_REQUIRED", "Server did not issue an authentication challenge")

        Payload = Challenge["payload"]
        if set(Payload) != {
            "instance_id",
            "server_nonce",
            "auth_epoch",
            "expires_at",
            "algorithm",
        }:
            raise SessionError("AUTH_FAILED", "Server challenge is invalid")
        InstanceId = Payload.get("instance_id")
        ServerNonce = Payload.get("server_nonce")
        AuthEpoch = Payload.get("auth_epoch")
        ExpiresAt = Payload.get("expires_at")
        if (
            not isinstance(InstanceId, str)
            or not REQUEST_ID_PATTERN.fullmatch(InstanceId)
            or not isinstance(ServerNonce, str)
            or not 32 <= len(ServerNonce) <= MAX_IDENTIFIER_LENGTH
            or not isinstance(AuthEpoch, int)
            or not isinstance(ExpiresAt, (int, float))
            or not math.isfinite(float(ExpiresAt))
            or time.time() > float(ExpiresAt)
        ):
            raise SessionError("AUTH_FAILED", "Server challenge is invalid")

        ClientNonce = secrets.token_urlsafe(32)
        Fields = [
            PROTOCOL_VERSION,
            InstanceId,
            AuthEpoch,
            Challenge["session_id"],
            Challenge["request_id"],
            ServerNonce,
            self.ClientInstanceId,
            ClientNonce,
        ]
        ClientProof = CreateProof(self.AuthToken, "CLIENT", Fields)
        send_message(
            Sock,
            BuildEnvelope(
                MessageType.AUTH,
                Challenge["request_id"],
                Challenge["session_id"],
                {
                    "client_instance_id": self.ClientInstanceId,
                    "client_nonce": ClientNonce,
                    "proof": ClientProof,
                },
            ),
        )

        AuthResultRaw = recv_message(
            Sock,
            max_frame_bytes=PREAUTH_MAX_FRAME_BYTES,
            header_timeout=HANDSHAKE_TIMEOUT_SECONDS,
            body_timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
        if AuthResultRaw is None:
            raise SessionError("AUTH_FAILED", "Server closed during authentication")
        AuthResult = ValidateEnvelope(AuthResultRaw)
        if AuthResult["message_type"] == MessageType.ERROR.value:
            self._RaiseRemoteError(AuthResult)
        if (
            AuthResult["message_type"] != MessageType.AUTH_OK.value
            or AuthResult["request_id"] != Challenge["request_id"]
            or AuthResult["session_id"] != Challenge["session_id"]
        ):
            raise SessionError("AUTH_FAILED", "Authentication response correlation failed")

        AuthResultPayload = AuthResult["payload"]
        if (
            set(AuthResultPayload) != {"instance_id", "auth_epoch", "proof"}
            or AuthResultPayload.get("instance_id") != InstanceId
            or AuthResultPayload.get("auth_epoch") != AuthEpoch
        ):
            raise SessionError("AUTH_FAILED", "Server authentication payload is invalid")

        ExpectedServerProof = CreateProof(self.AuthToken, "SERVER", Fields)
        SuppliedServerProof = AuthResultPayload.get("proof")
        if not isinstance(SuppliedServerProof, str) or not secrets.compare_digest(
            SuppliedServerProof, ExpectedServerProof
        ):
            raise SessionError("AUTH_FAILED", "Server authentication proof is invalid")

        self.InstanceId = InstanceId
        self.AuthEpoch = AuthEpoch
        self.SessionId = Challenge["session_id"]
        self.State = SessionState.AUTHENTICATED

    def BuildRequest(self, RequestId: str, Command: Dict[str, Any]) -> Dict[str, Any]:
        if self.State != SessionState.AUTHENTICATED:
            raise SessionError("AUTH_REQUIRED", "Connection is not authenticated", RequestId)
        return BuildEnvelope(MessageType.REQUEST, RequestId, self.SessionId, Command)

    def ValidateResponse(self, Envelope: Dict[str, Any], RequestId: str) -> Dict[str, Any]:
        Validated = ValidateEnvelope(Envelope)
        if Validated["session_id"] != self.SessionId:
            raise SessionError("SESSION_MISMATCH", "Response belongs to another session", RequestId)
        if Validated["request_id"] != RequestId:
            raise SessionError(
                "REQUEST_ID_MISMATCH", "Response request_id does not match", RequestId
            )
        if Validated["message_type"] == MessageType.ERROR.value:
            self._RaiseRemoteError(Validated)
        if Validated["message_type"] != MessageType.RESPONSE.value:
            raise SessionError("INVALID_MESSAGE_TYPE", "Expected a response envelope", RequestId)
        return cast(Dict[str, Any], Validated["payload"])

    def Close(self) -> None:
        self.State = SessionState.CLOSED
        self.SessionId = ""

    @staticmethod
    def _RaiseRemoteError(Envelope: Dict[str, Any]) -> None:
        Error = Envelope.get("payload", {}).get("error", {})
        Code = Error.get("code") if isinstance(Error, dict) else None
        Message = Error.get("message") if isinstance(Error, dict) else None
        raise SessionError(
            Code if isinstance(Code, str) else "REMOTE_ERROR",
            Message if isinstance(Message, str) else "Remote protocol error",
            str(Envelope.get("request_id", "protocol")),
            str(Envelope.get("session_id", "")),
        )
