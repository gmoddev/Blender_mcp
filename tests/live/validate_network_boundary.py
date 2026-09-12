"""Exercise the bounded network helper in Blender's embedded Python runtime."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.archive_boundary import ArtifactStore  # noqa: E402
from blender_mcp.core.network_boundary import (  # noqa: E402
    BoundedHttpClient,
    HttpPurposePolicy,
    NetworkBoundaryError,
    NetworkLimits,
)


class FakeResolver:
    def Resolve(self, Host: str, Port: int, TimeoutSeconds: float) -> tuple[str, ...]:
        del Host, Port, TimeoutSeconds
        return ("93.184.216.34",)


class FakeSocket:
    def settimeout(self, Value: float) -> None:
        assert Value > 0


class FakeResponse:
    def __init__(
        self,
        Status: int,
        Headers: list[tuple[str, str]],
        Chunks: list[bytes] | None = None,
    ) -> None:
        self.status = Status
        self._Headers = Headers
        self._Chunks = list(Chunks or [b""])

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._Headers)

    def read(self, Amount: int | None = None) -> bytes:
        del Amount
        return self._Chunks.pop(0) if self._Chunks else b""


class FakeConnection:
    def __init__(self, Response: FakeResponse) -> None:
        self.sock = FakeSocket()
        self._Response = Response
        self.Closed = False

    def request(
        self,
        Method: str,
        Url: str,
        Body: bytes | None = None,
        Headers: object = None,
    ) -> None:
        assert Method == "GET"
        assert Url.startswith("/models/")
        assert Body is None
        assert Headers is not None

    def getresponse(self) -> FakeResponse:
        return self._Response

    def close(self) -> None:
        self.Closed = True


class FakeConnectionBuilder:
    def __init__(self, Responses: list[FakeResponse]) -> None:
        self._Responses = list(Responses)
        self.Connections: list[FakeConnection] = []

    def __call__(self, Target: object, TimeoutSeconds: float) -> FakeConnection:
        del Target
        assert TimeoutSeconds > 0
        Connection = FakeConnection(self._Responses.pop(0))
        self.Connections.append(Connection)
        return Connection


BaseRoot = Path(tempfile.mkdtemp(prefix="blender_mcp_network_live_"))
ArtifactRoot = BaseRoot / "artifacts"
ArtifactRoot.mkdir()
Policy = HttpPurposePolicy(
    Name="BlenderLiveAsset",
    AllowedHosts=("assets.example.com", "cdn.example.com"),
    AllowedPathPrefixes=("/models",),
    AllowedContentTypes=("application/zip",),
    Limits=NetworkLimits(MaxResponseBytes=8, ChunkBytes=4),
)

try:
    Builder = FakeConnectionBuilder(
        [
            FakeResponse(302, [("Location", "https://cdn.example.com/models/hero.zip")]),
            FakeResponse(
                200,
                [("Content-Type", "application/zip"), ("Content-Length", "8")],
                [b"live", b"-zip", b""],
            ),
        ]
    )
    Result = BoundedHttpClient(Policy, FakeResolver(), Builder).Download(
        "https://assets.example.com/models/start",
        ArtifactStore(ArtifactRoot),
        "blender-live-network",
        "asset.zip",
    )
    assert Result.FinalUrl == "https://cdn.example.com/models/hero.zip"
    assert (Result.Workspace.Root / "asset.zip").read_bytes() == b"live-zip"
    assert all(Connection.Closed for Connection in Builder.Connections)
    Result.Workspace.Cleanup()

    OversizeBuilder = FakeConnectionBuilder(
        [
            FakeResponse(
                200,
                [("Content-Type", "application/zip")],
                [b"1234", b"5678", b"9", b""],
            )
        ]
    )
    try:
        BoundedHttpClient(Policy, FakeResolver(), OversizeBuilder).Download(
            "https://assets.example.com/models/large.zip",
            ArtifactStore(ArtifactRoot),
            "blender-live-oversize",
            "asset.zip",
        )
    except NetworkBoundaryError as Error:
        assert Error.Code == "NETWORK_RESPONSE_LIMIT_EXCEEDED"
    else:
        raise AssertionError("Oversized response was not denied")
    assert list(ArtifactRoot.iterdir()) == []
    print("[BlenderMCP:Network] Live network boundary validation passed")
finally:
    shutil.rmtree(BaseRoot, ignore_errors=True)
