"""Bounded length-prefixed JSON transport for Blender MCP."""

from __future__ import annotations

import json
import logging
import socket
import struct
import time
from typing import Any, Dict, Optional, cast

logger = logging.getLogger(__name__)

LENGTH_PREFIX_BYTES = 4
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 50_000


class ProtocolError(ValueError):
    """A bounded, caller-safe protocol failure."""

    def __init__(self, Code: str, Message: str) -> None:
        super().__init__(Message)
        self.Code = Code
        self.Message = Message


def _RejectJsonConstant(_Value: str) -> None:
    raise ProtocolError("INVALID_JSON", "Non-finite JSON numbers are not supported")


def send_message(
    sock: socket.socket,
    data: Dict[str, Any],
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> bool:
    """Serialize and send one bounded length-prefixed JSON object.

    The public function name is retained for compatibility. Protocol failures
    are raised as :class:`ProtocolError`; socket failures remain socket errors.
    """
    if not isinstance(data, dict):
        raise ProtocolError("INVALID_MESSAGE", "Protocol messages must be JSON objects")

    try:
        JsonBytes = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as Error:
        raise ProtocolError("INVALID_JSON", "Message is not JSON serializable") from Error

    FrameLength = len(JsonBytes)
    if FrameLength == 0:
        raise ProtocolError("INVALID_FRAME_LENGTH", "Empty protocol frames are not allowed")
    if FrameLength > max_frame_bytes:
        raise ProtocolError(
            "FRAME_TOO_LARGE",
            f"Protocol frame exceeds the {max_frame_bytes}-byte limit",
        )

    LengthPrefix = struct.pack(">I", FrameLength)
    try:
        sock.sendall(LengthPrefix + JsonBytes)
    except OSError:
        logger.warning("[BlenderMCP:Protocol] Socket send failed")
        raise
    return True


def recv_message(
    sock: socket.socket,
    max_frame_bytes: int = MAX_FRAME_BYTES,
    header_timeout: Optional[float] = None,
    body_timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Receive one bounded length-prefixed JSON object.

    ``None`` means the peer closed cleanly before a new frame. Truncation,
    malformed input, and oversized frames are explicit protocol errors.
    Optional deadlines are applied independently to the header and body and
    the caller's prior socket timeout is restored.
    """
    OriginalTimeout: Optional[float] = None
    ManageTimeout = header_timeout is not None or body_timeout is not None
    if ManageTimeout:
        OriginalTimeout = sock.gettimeout()

    try:
        if header_timeout is not None:
            sock.settimeout(header_timeout)
        HeaderDeadline = time.monotonic() + header_timeout if header_timeout is not None else None
        RawLength = _recv_n(sock, LENGTH_PREFIX_BYTES, HeaderDeadline)
        if RawLength is None:
            return None

        FrameLength = struct.unpack(">I", RawLength)[0]
        if FrameLength == 0:
            raise ProtocolError("INVALID_FRAME_LENGTH", "Zero-length frames are not allowed")
        if FrameLength > max_frame_bytes:
            raise ProtocolError(
                "FRAME_TOO_LARGE",
                f"Protocol frame exceeds the {max_frame_bytes}-byte limit",
            )

        if body_timeout is not None:
            sock.settimeout(body_timeout)
        BodyDeadline = time.monotonic() + body_timeout if body_timeout is not None else None
        MessageBytes = _recv_n(sock, FrameLength, BodyDeadline)
        if MessageBytes is None:
            raise ProtocolError("TRUNCATED_FRAME", "Connection closed before the frame completed")

        try:
            Value = json.loads(
                MessageBytes.decode("utf-8"),
                parse_constant=_RejectJsonConstant,
            )
        except UnicodeDecodeError as Error:
            raise ProtocolError("INVALID_UTF8", "Frame body is not valid UTF-8") from Error
        except json.JSONDecodeError as Error:
            raise ProtocolError("INVALID_JSON", "Frame body is not valid JSON") from Error
        except RecursionError as Error:
            raise ProtocolError("JSON_TOO_DEEP", "JSON nesting limit exceeded") from Error

        if not isinstance(Value, dict):
            raise ProtocolError("INVALID_MESSAGE", "Protocol messages must be JSON objects")
        _ValidateJsonShape(Value)
        return cast(Dict[str, Any], Value)
    except socket.timeout:
        raise
    finally:
        if ManageTimeout:
            sock.settimeout(OriginalTimeout)


def _recv_n(
    sock: socket.socket,
    n: int,
    Deadline: Optional[float] = None,
) -> Optional[bytes]:
    """Receive exactly ``n`` bytes without repeated immutable concatenation."""
    if n < 0:
        raise ProtocolError("INVALID_FRAME_LENGTH", "Negative receive length is invalid")
    if n == 0:
        return b""

    Chunks = []
    Received = 0
    while Received < n:
        if Deadline is not None:
            Remaining = Deadline - time.monotonic()
            if Remaining <= 0:
                raise socket.timeout("Frame deadline exceeded")
            sock.settimeout(Remaining)
        Chunk = sock.recv(n - Received)
        if not Chunk:
            return None
        Chunks.append(Chunk)
        Received += len(Chunk)
    return b"".join(Chunks)


def _ValidateJsonShape(Value: Any) -> None:
    """Bound aggregate JSON complexity after the frame-size gate."""
    Pending = [(Value, 1)]
    NodeCount = 0
    while Pending:
        Current, Depth = Pending.pop()
        NodeCount += 1
        if NodeCount > MAX_JSON_NODES:
            raise ProtocolError("JSON_TOO_COMPLEX", "JSON node limit exceeded")
        if Depth > MAX_JSON_DEPTH:
            raise ProtocolError("JSON_TOO_DEEP", "JSON nesting limit exceeded")
        if isinstance(Current, dict):
            Pending.extend((Key, Depth + 1) for Key in Current.keys())
            Pending.extend((Item, Depth + 1) for Item in Current.values())
        elif isinstance(Current, list):
            Pending.extend((Item, Depth + 1) for Item in Current)
