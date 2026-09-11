"""Exercise command lifecycle security invariants against a live Blender process."""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from stdio_bridge import MCPBridge  # noqa: E402

Host = "127.0.0.1"
Port = int(os.environ["BLENDER_MCP_LIVE_TEST_PORT"])
AuthToken = os.environ["BLENDER_MCP_AUTH_TOKEN"]


def NewBridge(Token: str = AuthToken) -> MCPBridge:
    return MCPBridge(host=Host, port=Port, auth_token=Token)


def NewNamespacePeer(Bridge: MCPBridge) -> MCPBridge:
    Peer = NewBridge()
    Peer.ClientInstanceId = Bridge.ClientInstanceId
    return Peer


def RequestId(Label: str) -> str:
    return f"live-{Label}-{uuid.uuid4().hex[:12]}"


def Send(
    Bridge: MCPBridge,
    Tool: str,
    Params: dict[str, Any],
    Request: str,
) -> dict[str, Any]:
    return Bridge.send_to_blender(
        {"tool": Tool, "params": Params, "request_id": Request},
        retries=0,
    )


def RequireSuccess(Response: dict[str, Any], Label: str) -> dict[str, Any]:
    if Response.get("status") != "success":
        raise AssertionError(f"{Label} failed: {Response}")
    Result = Response.get("result")
    if not isinstance(Result, dict):
        raise AssertionError(f"{Label} returned no result object: {Response}")
    return Result


def RequireError(Response: dict[str, Any], Code: str, Label: str) -> dict[str, Any]:
    Error = Response.get("error")
    if Response.get("status") != "error" or not isinstance(Error, dict):
        raise AssertionError(f"{Label} did not fail closed: {Response}")
    if Error.get("code") != Code:
        raise AssertionError(f"{Label} returned {Error.get('code')}, expected {Code}: {Response}")
    return Error


def TryGetStatus(Bridge: MCPBridge, TargetRequestId: str) -> dict[str, Any] | None:
    Response = Send(
        Bridge,
        "manage_command_lifecycle",
        {
            "action": "GET_STATUS",
            "target_request_id": TargetRequestId,
            "include_result": True,
        },
        RequestId("status"),
    )
    Error = Response.get("error")
    if Response.get("status") == "error" and isinstance(Error, dict):
        if Error.get("code") == "REQUEST_NOT_FOUND":
            return None
    Result = RequireSuccess(Response, f"status for {TargetRequestId}")
    Snapshot = Result.get("request")
    if not isinstance(Snapshot, dict):
        raise AssertionError(f"status response is malformed: {Result}")
    return Snapshot


def GetStatus(Bridge: MCPBridge, TargetRequestId: str) -> dict[str, Any]:
    Snapshot = TryGetStatus(Bridge, TargetRequestId)
    if Snapshot is None:
        raise AssertionError(f"request is not retained: {TargetRequestId}")
    return Snapshot


def WaitForState(
    Bridge: MCPBridge,
    TargetRequestId: str,
    Predicate: Callable[[str], bool],
    Label: str,
    TimeoutSeconds: float = 3.0,
) -> dict[str, Any]:
    Deadline = time.monotonic() + TimeoutSeconds
    LastSnapshot: dict[str, Any] = {}
    while time.monotonic() < Deadline:
        Candidate = TryGetStatus(Bridge, TargetRequestId)
        if Candidate is None:
            time.sleep(0.01)
            continue
        LastSnapshot = Candidate
        if Predicate(str(LastSnapshot.get("state"))):
            return LastSnapshot
        time.sleep(0.01)
    raise AssertionError(f"{Label} did not reach expected state: {LastSnapshot}")


def GetCount(Bridge: MCPBridge) -> int:
    Result = RequireSuccess(
        Send(
            Bridge,
            "_live_lifecycle_probe",
            {"action": "GET_COUNT", "timeout_seconds": 2.0},
            RequestId("count"),
        ),
        "get mutation count",
    )
    return int(Result["count"])


def RunAsync(
    Bridge: MCPBridge,
    Tool: str,
    Params: dict[str, Any],
    Request: str,
) -> tuple[threading.Thread, list[dict[str, Any]]]:
    Responses: list[dict[str, Any]] = []

    def Worker() -> None:
        Responses.append(Send(Bridge, Tool, Params, Request))

    WorkerThread = threading.Thread(target=Worker, name=f"LiveValidation-{Request}")
    WorkerThread.start()
    return WorkerThread, Responses


def Join(WorkerThread: threading.Thread, Responses: list[dict[str, Any]]) -> dict[str, Any]:
    WorkerThread.join(5.0)
    if WorkerThread.is_alive() or len(Responses) != 1:
        raise AssertionError("live request worker did not return exactly one response")
    return Responses[0]


def CloseAll(*Bridges: MCPBridge) -> None:
    for Bridge in Bridges:
        Bridge.CloseConnection()


def Validate() -> None:
    Observer = NewBridge()
    First = NewBridge()
    Second = NewBridge()
    Lost = NewBridge()
    OpenPeers: list[MCPBridge] = []
    try:
        if not Observer.connect():
            raise AssertionError("authenticated observer could not connect")

        WrongPrefix = "A" if AuthToken[0] != "A" else "B"
        WrongToken = WrongPrefix + AuthToken[1:]
        Unauthorized = NewBridge(WrongToken)
        try:
            if Unauthorized.connect():
                raise AssertionError("wrong credential authenticated")
            if Unauthorized._LastErrorCode != "AUTH_FAILED":
                raise AssertionError(f"wrong credential returned {Unauthorized._LastErrorCode}")
        finally:
            Unauthorized.CloseConnection()
        print("[BlenderMCP:LiveValidation] PASS authentication fails closed", flush=True)

        if GetCount(Observer) != 0:
            raise AssertionError("probe scene did not start clean")

        BlockRequest = RequestId("pending-block")
        BlockThread, BlockResponses = RunAsync(
            First,
            "_live_lifecycle_probe",
            {"action": "BLOCK", "duration_seconds": 0.45, "timeout_seconds": 2.0},
            BlockRequest,
        )
        FirstPeer = NewNamespacePeer(First)
        OpenPeers.append(FirstPeer)
        WaitForState(FirstPeer, BlockRequest, lambda State: State == "running", "block request")
        FirstPeer.CloseConnection()

        PendingRequest = RequestId("pending-timeout")
        PendingResponse = Send(
            Second,
            "_live_lifecycle_probe",
            {"action": "MUTATE", "duration_seconds": 0.0, "timeout_seconds": 0.1},
            PendingRequest,
        )
        PendingError = RequireError(
            PendingResponse,
            "COMMAND_TIMED_OUT_PENDING",
            "pending timeout",
        )
        if PendingError.get("command_state") != "timed_out_pending":
            raise AssertionError(f"pending timeout state was ambiguous: {PendingResponse}")
        RequireSuccess(Join(BlockThread, BlockResponses), "main-thread blocker")
        PendingSnapshot = GetStatus(Second, PendingRequest)
        if PendingSnapshot.get("state") != "timed_out_pending" or GetCount(Observer) != 0:
            raise AssertionError(f"pending timeout callable ran: {PendingSnapshot}")
        if TryGetStatus(Observer, PendingRequest) is not None:
            raise AssertionError("another bridge queried a request outside its namespace")
        Second.CloseConnection()
        print("[BlenderMCP:LiveValidation] PASS pending timeout tombstone", flush=True)

        RunningRequest = RequestId("running-timeout")
        RunningResponse = Send(
            First,
            "_live_lifecycle_probe",
            {"action": "MUTATE", "duration_seconds": 0.5, "timeout_seconds": 0.2},
            RunningRequest,
        )
        RunningError = RequireError(RunningResponse, "REQUEST_INDETERMINATE", "running timeout")
        if RunningError.get("command_state") != "running_after_timeout":
            raise AssertionError(f"running timeout lost state: {RunningResponse}")
        WaitForState(
            First,
            RunningRequest,
            lambda State: State == "completed_late",
            "late completion",
        )
        if GetCount(Observer) != 1:
            raise AssertionError("running mutation did not complete exactly once")

        RunningParams = {
            "action": "MUTATE",
            "duration_seconds": 0.5,
            "timeout_seconds": 0.2,
        }
        RequireSuccess(
            Send(First, "_live_lifecycle_probe", RunningParams, RunningRequest),
            "duplicate replay",
        )
        ConflictResponse = Send(
            First,
            "_live_lifecycle_probe",
            {"action": "MUTATE", "duration_seconds": 0.1, "timeout_seconds": 0.2},
            RunningRequest,
        )
        RequireError(ConflictResponse, "REQUEST_ID_CONFLICT", "request ID conflict")
        if GetCount(Observer) != 1:
            raise AssertionError("duplicate or conflicting request re-executed")
        print("[BlenderMCP:LiveValidation] PASS duplicate and conflict handling", flush=True)

        First.CloseConnection()
        Second.CloseConnection()
        LostRequest = RequestId("response-loss")
        LostParams = {
            "action": "MUTATE",
            "duration_seconds": 0.5,
            "timeout_seconds": 2.0,
        }
        LostThread, LostResponses = RunAsync(
            Lost,
            "_live_lifecycle_probe",
            LostParams,
            LostRequest,
        )
        LostPeer = NewNamespacePeer(Lost)
        OpenPeers.append(LostPeer)
        WaitForState(
            LostPeer,
            LostRequest,
            lambda State: State == "running",
            "lost response",
        )
        Lost.CloseConnection()
        LostResponse = Join(LostThread, LostResponses)
        if LostResponse.get("code") != "REQUEST_INDETERMINATE":
            raise AssertionError(f"lost response was not indeterminate: {LostResponse}")
        LostPeer.CloseConnection()
        WaitForState(
            Lost,
            LostRequest,
            lambda State: State in {"completed", "completed_late"},
            "response-loss reconciliation",
        )
        if GetCount(Observer) != 2:
            raise AssertionError("response-loss mutation did not complete exactly once")
        RequireSuccess(
            Send(Lost, "_live_lifecycle_probe", LostParams, LostRequest),
            "reconciled duplicate replay",
        )
        if GetCount(Observer) != 2:
            raise AssertionError("reconciled duplicate re-executed")
        print("[BlenderMCP:LiveValidation] PASS reconnect and reconcile", flush=True)

        CancelBlockRequest = RequestId("cancel-block")
        CancelBlockThread, CancelBlockResponses = RunAsync(
            First,
            "_live_lifecycle_probe",
            {"action": "BLOCK", "duration_seconds": 0.45, "timeout_seconds": 2.0},
            CancelBlockRequest,
        )
        FirstPeer = NewNamespacePeer(First)
        OpenPeers.append(FirstPeer)
        WaitForState(
            FirstPeer,
            CancelBlockRequest,
            lambda State: State == "running",
            "cancel blocker",
        )
        FirstPeer.CloseConnection()
        CancelRequest = RequestId("cancel-pending")
        CancelThread, CancelResponses = RunAsync(
            Second,
            "_live_lifecycle_probe",
            {"action": "MUTATE", "duration_seconds": 0.0, "timeout_seconds": 2.0},
            CancelRequest,
        )
        SecondPeer = NewNamespacePeer(Second)
        OpenPeers.append(SecondPeer)
        WaitForState(SecondPeer, CancelRequest, lambda State: State == "pending", "cancel target")
        CrossClientCancel = Send(
            Observer,
            "manage_command_lifecycle",
            {"action": "CANCEL", "target_request_id": CancelRequest},
            RequestId("cross-client-cancel"),
        )
        RequireError(CrossClientCancel, "REQUEST_NOT_FOUND", "cross-client cancel")
        CancelResult = RequireSuccess(
            Send(
                SecondPeer,
                "manage_command_lifecycle",
                {"action": "CANCEL", "target_request_id": CancelRequest},
                RequestId("cancel"),
            ),
            "cancel pending request",
        )
        SecondPeer.CloseConnection()
        CancelSnapshot = CancelResult.get("request", {})
        if CancelSnapshot.get("state") != "cancelled" or not CancelSnapshot.get("cancelled"):
            raise AssertionError(f"cancel did not tombstone pending request: {CancelResult}")
        RequireError(Join(CancelThread, CancelResponses), "REQUEST_CANCELLED", "cancelled request")
        RequireSuccess(Join(CancelBlockThread, CancelBlockResponses), "cancel blocker")
        if GetCount(Observer) != 2:
            raise AssertionError("cancelled callable executed")
        print("[BlenderMCP:LiveValidation] PASS pending cancellation", flush=True)
    finally:
        CloseAll(Observer, First, Second, Lost, *OpenPeers)


if __name__ == "__main__":
    Validate()
    print("[BlenderMCP:LiveValidation] ALL CHECKS PASSED", flush=True)
