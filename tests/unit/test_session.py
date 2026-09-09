"""Authentication, session binding, and correlation tests."""

from __future__ import annotations

import base64
import socket
import threading
import time

import pytest

from blender_mcp.core.protocol import recv_message, send_message
from blender_mcp.core.session import (
    PROTOCOL_VERSION,
    BuildEnvelope,
    ClientSession,
    CreateProof,
    MessageType,
    ServerSession,
    SessionError,
    SessionState,
    ValidateAuthToken,
)

TOKEN_A = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
TOKEN_B = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii").rstrip("=")


def BuildAuth(Server: ServerSession, AuthToken: str) -> dict:
    ClientNonce = "client-nonce-with-at-least-32-bytes"
    Fields = [
        PROTOCOL_VERSION,
        Server.InstanceId,
        Server.AuthEpoch,
        Server.SessionId,
        Server.RequestId,
        Server.ServerNonce,
        ClientNonce,
    ]
    return BuildEnvelope(
        MessageType.AUTH,
        Server.RequestId,
        Server.SessionId,
        {"client_nonce": ClientNonce, "proof": CreateProof(AuthToken, "CLIENT", Fields)},
    )


class TestServerSession:
    def test_weak_manual_token_is_rejected(self) -> None:
        with pytest.raises(SessionError) as Error:
            ValidateAuthToken("A" * 43)
        assert Error.value.Code == "AUTH_CONFIGURATION_ERROR"

    def test_authenticated_request_is_accepted(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        Server.Authenticate(BuildAuth(Server, TOKEN_A))
        Request = BuildEnvelope(
            MessageType.REQUEST,
            "request-1",
            Server.SessionId,
            {"tool": "get_server_status", "params": {"action": "get_server_status"}},
        )
        assert Server.ValidateRequest(Request)["tool"] == "get_server_status"

    def test_wrong_token_is_rejected(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        with pytest.raises(SessionError) as Error:
            Server.Authenticate(BuildAuth(Server, TOKEN_B))
        assert Error.value.Code == "AUTH_FAILED"

    def test_request_before_authentication_is_rejected(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        Request = BuildEnvelope(
            MessageType.REQUEST, "request-1", Server.SessionId, {"tool": "list_all_tools"}
        )
        with pytest.raises(SessionError) as Error:
            Server.ValidateRequest(Request)
        assert Error.value.Code == "AUTH_REQUIRED"

    def test_proof_from_instance_a_is_rejected_by_b(self) -> None:
        ServerA = ServerSession(TOKEN_A, "instance-a", 1)
        CapturedAuth = BuildAuth(ServerA, TOKEN_A)
        ServerB = ServerSession(TOKEN_A, "instance-b", 1)
        CapturedAuth["request_id"] = ServerB.RequestId
        CapturedAuth["session_id"] = ServerB.SessionId
        with pytest.raises(SessionError) as Error:
            ServerB.Authenticate(CapturedAuth)
        assert Error.value.Code == "AUTH_FAILED"

    def test_expired_challenge_is_rejected(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1, LifetimeSeconds=-1)
        with pytest.raises(SessionError) as Error:
            Server.Authenticate(BuildAuth(Server, TOKEN_A))
        assert Error.value.Code == "AUTH_EXPIRED"

    def test_wrong_session_request_is_rejected(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        Server.Authenticate(BuildAuth(Server, TOKEN_A))
        Request = BuildEnvelope(MessageType.REQUEST, "request-1", "wrong-session", {})
        with pytest.raises(SessionError) as Error:
            Server.ValidateRequest(Request)
        assert Error.value.Code == "SESSION_MISMATCH"

    def test_unsupported_protocol_is_rejected(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        Auth = BuildAuth(Server, TOKEN_A)
        Auth["protocol_version"] = PROTOCOL_VERSION + 1
        with pytest.raises(SessionError) as Error:
            Server.Authenticate(Auth)
        assert Error.value.Code == "UNSUPPORTED_PROTOCOL"

    def test_auth_payload_cannot_smuggle_tool_fields(self) -> None:
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        Auth = BuildAuth(Server, TOKEN_A)
        Auth["payload"]["tool"] = "execute_blender_code"
        with pytest.raises(SessionError) as Error:
            Server.Authenticate(Auth)
        assert Error.value.Code == "AUTH_FAILED"


class TestClientSession:
    def test_mutual_handshake_and_response_correlation(self) -> None:
        ClientSock, ServerSock = socket.socketpair()
        Server = ServerSession(TOKEN_A, "instance-a", 3)

        def Serve() -> None:
            send_message(ServerSock, Server.BuildChallenge())
            Auth = recv_message(ServerSock)
            assert Auth is not None
            send_message(ServerSock, Server.Authenticate(Auth))
            Request = recv_message(ServerSock)
            assert Request is not None
            Payload = Server.ValidateRequest(Request)
            send_message(
                ServerSock,
                BuildEnvelope(
                    MessageType.RESPONSE,
                    Request["request_id"],
                    Server.SessionId,
                    {"echo": Payload},
                ),
            )

        Thread = threading.Thread(target=Serve)
        Thread.start()
        try:
            Client = ClientSession(TOKEN_A)
            Client.PerformHandshake(ClientSock)
            Request = Client.BuildRequest("request-42", {"value": 42})
            send_message(ClientSock, Request)
            Response = recv_message(ClientSock)
            assert Response is not None
            assert Client.ValidateResponse(Response, "request-42") == {"echo": {"value": 42}}
            assert Client.InstanceId == "instance-a"
            assert Client.AuthEpoch == 3
        finally:
            ClientSock.close()
            ServerSock.close()
            Thread.join(timeout=2)

    def test_response_request_id_mismatch_is_rejected(self) -> None:
        Client = ClientSession(TOKEN_A)
        Client.SessionId = "session-a"
        Client.State = SessionState.AUTHENTICATED
        Response = BuildEnvelope(MessageType.RESPONSE, "wrong", "session-a", {})
        with pytest.raises(SessionError) as Error:
            Client.ValidateResponse(Response, "expected")
        assert Error.value.Code == "REQUEST_ID_MISMATCH"

    def test_challenge_expiry_is_checked_by_client(self) -> None:
        ClientSock, ServerSock = socket.socketpair()
        Server = ServerSession(TOKEN_A, "instance-a", 1)
        Challenge = Server.BuildChallenge()
        Challenge["payload"]["expires_at"] = time.time() - 1
        send_message(ServerSock, Challenge)
        try:
            with pytest.raises(SessionError) as Error:
                ClientSession(TOKEN_A).PerformHandshake(ClientSock)
            assert Error.value.Code == "AUTH_FAILED"
        finally:
            ClientSock.close()
            ServerSock.close()
