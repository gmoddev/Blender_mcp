"""Security tests for purpose-scoped HTTPS and bounded downloads."""

from __future__ import annotations

import sys
import threading
import ssl
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("bpy", MagicMock())
sys.modules.setdefault("mathutils", MagicMock())
sys.modules.setdefault("mathutils.bvhtree", MagicMock())
sys.modules.setdefault("bmesh", MagicMock())

from blender_mcp.core.archive_boundary import ArtifactStore  # noqa: E402
from blender_mcp.core.network_boundary import (  # noqa: E402
    AuthorizeUrl,
    BoundedDnsResolver,
    BoundedHttpClient,
    HttpPurposePolicy,
    NetworkBoundaryError,
    NetworkLimits,
    RequireConnectedPeer,
    ResolveNetworkTarget,
    _BuildTlsContext,
    _PinnedHttpsConnection,
)


class FakeResolver:
    def __init__(self, Addresses: tuple[str, ...] = ("93.184.216.34",)):
        self.Addresses = Addresses
        self.Calls: list[tuple[str, int, float]] = []

    def Resolve(self, Host: str, Port: int, TimeoutSeconds: float) -> tuple[str, ...]:
        self.Calls.append((Host, Port, TimeoutSeconds))
        return self.Addresses


class FakeSocket:
    def __init__(self) -> None:
        self.Timeouts: list[float] = []

    def settimeout(self, Value: float) -> None:
        self.Timeouts.append(Value)


class FakeResponse:
    def __init__(
        self,
        Status: int = 200,
        Headers: list[tuple[str, str]] | None = None,
        Chunks: list[bytes] | None = None,
    ) -> None:
        self.status = Status
        self.Headers = Headers or []
        self.Chunks = list(Chunks or [b""])

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self.Headers)

    def read(self, Amount: int | None = None) -> bytes:
        del Amount
        return self.Chunks.pop(0) if self.Chunks else b""


class FakeConnection:
    def __init__(self, Response: FakeResponse):
        self.Response = Response
        self.sock = FakeSocket()
        self.Requests: list[tuple[str, str, object, object]] = []
        self.Closed = False

    def request(
        self,
        Method: str,
        Url: str,
        Body: bytes | None = None,
        Headers: object = None,
    ) -> None:
        self.Requests.append((Method, Url, Body, Headers))

    def getresponse(self) -> FakeResponse:
        return self.Response

    def close(self) -> None:
        self.Closed = True


class FakeConnectionBuilder:
    def __init__(self, Responses: list[FakeResponse]):
        self.Responses = list(Responses)
        self.Targets: list[object] = []
        self.Connections: list[FakeConnection] = []

    def __call__(self, Target: object, TimeoutSeconds: float) -> FakeConnection:
        self.Targets.append((Target, TimeoutSeconds))
        Connection = FakeConnection(self.Responses.pop(0))
        self.Connections.append(Connection)
        return Connection


def GetPolicy(**Overrides: object) -> HttpPurposePolicy:
    Values = {
        "Name": "ProviderAsset",
        "AllowedHosts": ("assets.example.com", "cdn.example.com"),
        "AllowedPathPrefixes": ("/models",),
        "AllowedContentTypes": ("application/zip",),
        "Limits": NetworkLimits(MaxResponseBytes=32, ChunkBytes=8),
    }
    Values.update(Overrides)
    return HttpPurposePolicy(**Values)


def AssertDenied(Code: str, Callback: object) -> NetworkBoundaryError:
    with pytest.raises(NetworkBoundaryError) as Captured:
        Callback()
    assert Captured.value.Code == Code
    assert str(Captured.value) == Captured.value.PublicMessage
    return Captured.value


def test_authorizes_exact_https_host_port_and_component_path() -> None:
    Result = AuthorizeUrl("https://assets.example.com/models/hero.zip?id=1", GetPolicy())

    assert Result.Host == "assets.example.com"
    assert Result.Port == 443
    assert Result.RequestTarget == "/models/hero.zip?id=1"


@pytest.mark.parametrize(
    "Url",
    [
        "http://assets.example.com/models/hero.zip",
        "file:///models/hero.zip",
        "//assets.example.com/models/hero.zip",
        "https://user:secret@assets.example.com/models/hero.zip",
        "https://assets.example.com:444/models/hero.zip",
        "https://evil.example/models/hero.zip",
        "https://93.184.216.34/models/hero.zip",
        "https://assets.example.com/models/hero.zip#fragment",
    ],
)
def test_non_policy_destinations_are_denied_before_dns(Url: str) -> None:
    AssertDenied(
        "NETWORK_URL_DENIED"
        if "http:" in Url or "file:" in Url or Url.startswith("//") or "@" in Url or "#" in Url
        else "NETWORK_DESTINATION_DENIED"
        if "444" in Url or "evil" in Url
        else "NETWORK_HOST_DENIED",
        lambda: AuthorizeUrl(Url, GetPolicy()),
    )


@pytest.mark.parametrize(
    "Url",
    [
        "https://assets.example.com/other/hero.zip",
        "https://assets.example.com/modelish/hero.zip",
        "https://assets.example.com/models/../admin",
        "https://assets.example.com/models//hero.zip",
        "https://assets.example.com/models/%2e%2e/admin",
        "https://assets.example.com/models\\hero.zip",
    ],
)
def test_ambiguous_or_out_of_purpose_paths_are_denied(Url: str) -> None:
    AssertDenied("NETWORK_PATH_DENIED", lambda: AuthorizeUrl(Url, GetPolicy()))


def test_query_policy_fails_closed() -> None:
    Policy = GetPolicy(AllowQuery=False)
    AssertDenied(
        "NETWORK_QUERY_DENIED",
        lambda: AuthorizeUrl("https://assets.example.com/models/hero.zip?id=1", Policy),
    )


def test_explicit_root_path_policy_is_supported() -> None:
    Policy = GetPolicy(AllowedPathPrefixes=("/",))
    assert AuthorizeUrl("https://assets.example.com", Policy).RequestTarget == "/"


@pytest.mark.parametrize(
    "Address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "not-an-address",
    ],
)
def test_non_public_dns_answers_are_denied(Address: str) -> None:
    AssertDenied(
        "NETWORK_DNS_ADDRESS_DENIED",
        lambda: ResolveNetworkTarget(
            "https://assets.example.com/models/hero.zip",
            GetPolicy(),
            FakeResolver((Address,)),
            1.0,
        ),
    )


def test_one_private_dns_answer_denies_the_entire_resolution() -> None:
    AssertDenied(
        "NETWORK_DNS_ADDRESS_DENIED",
        lambda: ResolveNetworkTarget(
            "https://assets.example.com/models/hero.zip",
            GetPolicy(),
            FakeResolver(("93.184.216.34", "127.0.0.1")),
            1.0,
        ),
    )


def test_connected_peer_must_match_authorized_dns_result() -> None:
    assert RequireConnectedPeer("93.184.216.34", ("93.184.216.34",)) == "93.184.216.34"
    AssertDenied(
        "NETWORK_CONNECTED_PEER_MISMATCH",
        lambda: RequireConnectedPeer("93.184.216.35", ("93.184.216.34",)),
    )


def test_bounded_dns_timeout_and_capacity_fail_closed() -> None:
    Release = threading.Event()

    def BlockingLookup(Host: str, Port: int) -> tuple[str, ...]:
        del Host, Port
        Release.wait(1.0)
        return ("93.184.216.34",)

    Resolver = BoundedDnsResolver(MaxOutstanding=1, Lookup=BlockingLookup)
    AssertDenied("NETWORK_DNS_TIMEOUT", lambda: Resolver.Resolve("assets.example.com", 443, 0.01))
    AssertDenied(
        "NETWORK_DNS_CAPACITY_EXCEEDED",
        lambda: Resolver.Resolve("assets.example.com", 443, 0.01),
    )
    Release.set()


def test_download_streams_to_opaque_workspace_and_cleans_by_context(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Builder = FakeConnectionBuilder(
        [
            FakeResponse(
                Headers=[("Content-Type", "application/zip"), ("Content-Length", "8")],
                Chunks=[b"1234", b"5678", b""],
            )
        ]
    )
    Client = BoundedHttpClient(GetPolicy(), FakeResolver(), Builder)

    Result = Client.Download(
        "https://assets.example.com/models/hero.zip",
        ArtifactStore(ArtifactRoot),
        "request-1",
        "asset.zip",
    )

    assert Result.BytesWritten == 8
    assert (Result.Workspace.Root / "asset.zip").read_bytes() == b"12345678"
    assert Builder.Connections[0].Closed is True
    with Result.Workspace:
        WorkspaceRoot = Result.Workspace.Root
    assert not WorkspaceRoot.exists()


def test_redirect_is_reauthorized_and_resolved_again(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Resolver = FakeResolver()
    Builder = FakeConnectionBuilder(
        [
            FakeResponse(
                Status=302, Headers=[("Location", "https://cdn.example.com/models/hero.zip")]
            ),
            FakeResponse(
                Headers=[("Content-Type", "application/zip")],
                Chunks=[b"zip", b""],
            ),
        ]
    )

    Result = BoundedHttpClient(GetPolicy(), Resolver, Builder).Download(
        "https://assets.example.com/models/start",
        ArtifactStore(ArtifactRoot),
        "request-2",
        "asset.zip",
    )

    assert [Call[0] for Call in Resolver.Calls] == ["assets.example.com", "cdn.example.com"]
    assert Result.FinalUrl == "https://cdn.example.com/models/hero.zip"
    Result.Workspace.Cleanup()


def test_redirect_to_unapproved_host_is_denied_without_workspace(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Builder = FakeConnectionBuilder(
        [FakeResponse(Status=302, Headers=[("Location", "https://evil.example/steal")])]
    )

    AssertDenied(
        "NETWORK_DESTINATION_DENIED",
        lambda: BoundedHttpClient(GetPolicy(), FakeResolver(), Builder).Download(
            "https://assets.example.com/models/start",
            ArtifactStore(ArtifactRoot),
            "request-3",
            "asset.zip",
        ),
    )
    assert list(ArtifactRoot.iterdir()) == []


def test_declared_and_streamed_oversize_responses_are_denied_and_cleaned(tmp_path: Path) -> None:
    for Name, Headers, Chunks in (
        ("declared", [("Content-Type", "application/zip"), ("Content-Length", "33")], [b""]),
        ("streamed", [("Content-Type", "application/zip")], [b"A" * 24, b"B" * 24, b""]),
    ):
        ArtifactRoot = tmp_path / Name
        ArtifactRoot.mkdir()
        Builder = FakeConnectionBuilder([FakeResponse(Headers=Headers, Chunks=Chunks)])
        AssertDenied(
            "NETWORK_RESPONSE_LIMIT_EXCEEDED",
            lambda: BoundedHttpClient(GetPolicy(), FakeResolver(), Builder).Download(
                "https://assets.example.com/models/hero.zip",
                ArtifactStore(ArtifactRoot),
                f"request-{Name}",
                "asset.zip",
            ),
        )
        assert list(ArtifactRoot.iterdir()) == []


def test_truncated_declared_response_is_denied_and_cleaned(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Builder = FakeConnectionBuilder(
        [
            FakeResponse(
                Headers=[("Content-Type", "application/zip"), ("Content-Length", "8")],
                Chunks=[b"short", b""],
            )
        ]
    )

    AssertDenied(
        "NETWORK_RESPONSE_LENGTH_MISMATCH",
        lambda: BoundedHttpClient(GetPolicy(), FakeResolver(), Builder).Download(
            "https://assets.example.com/models/hero.zip",
            ArtifactStore(ArtifactRoot),
            "request-truncated",
            "asset.zip",
        ),
    )
    assert list(ArtifactRoot.iterdir()) == []


@pytest.mark.parametrize(
    ("Headers", "Code"),
    [
        ([("Content-Type", "text/html")], "NETWORK_CONTENT_TYPE_DENIED"),
        (
            [("Content-Type", "application/zip"), ("Content-Encoding", "gzip")],
            "NETWORK_CONTENT_ENCODING_DENIED",
        ),
        (
            [
                ("Content-Type", "application/zip"),
                ("Content-Length", "1"),
                ("Transfer-Encoding", "chunked"),
            ],
            "NETWORK_BODY_FRAMING_DENIED",
        ),
        (
            [("Content-Type", "application/zip"), ("Content-Length", "1"), ("Content-Length", "2")],
            "NETWORK_BODY_FRAMING_DENIED",
        ),
        (
            [("Content-Type", "application/zip"), ("Content-Length", "+1")],
            "NETWORK_BODY_FRAMING_DENIED",
        ),
    ],
)
def test_response_metadata_fails_closed_before_workspace(
    tmp_path: Path, Headers: list[tuple[str, str]], Code: str
) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Builder = FakeConnectionBuilder([FakeResponse(Headers=Headers)])

    AssertDenied(
        Code,
        lambda: BoundedHttpClient(GetPolicy(), FakeResolver(), Builder).Download(
            "https://assets.example.com/models/hero.zip",
            ArtifactStore(ArtifactRoot),
            "request-metadata",
            "asset.zip",
        ),
    )
    assert list(ArtifactRoot.iterdir()) == []


def test_total_deadline_denies_and_cleans_partial_output(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Builder = FakeConnectionBuilder(
        [FakeResponse(Headers=[("Content-Type", "application/zip")], Chunks=[b"data", b""])]
    )
    Times = iter((0.0, 0.0, 0.0, 121.0))
    Client = BoundedHttpClient(GetPolicy(), FakeResolver(), Builder, lambda: next(Times))

    AssertDenied(
        "NETWORK_TOTAL_TIMEOUT",
        lambda: Client.Download(
            "https://assets.example.com/models/hero.zip",
            ArtifactStore(ArtifactRoot),
            "request-timeout",
            "asset.zip",
        ),
    )
    assert list(ArtifactRoot.iterdir()) == []


@pytest.mark.parametrize("FileName", ["../asset.zip", "folder/asset.zip", "CON.zip", "asset zip"])
def test_artifact_filename_is_single_safe_component(tmp_path: Path, FileName: str) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    AssertDenied(
        "NETWORK_ARTIFACT_NAME_DENIED",
        lambda: BoundedHttpClient(GetPolicy(), FakeResolver(), FakeConnectionBuilder([])).Download(
            "https://assets.example.com/models/hero.zip",
            ArtifactStore(ArtifactRoot),
            "request-name",
            FileName,
        ),
    )
    assert list(ArtifactRoot.iterdir()) == []


def test_network_limits_and_policy_configuration_validate() -> None:
    with pytest.raises(ValueError):
        NetworkLimits(MaxResponseBytes=0)
    with pytest.raises(ValueError):
        NetworkLimits(TotalSeconds=0)
    with pytest.raises(ValueError):
        GetPolicy(AllowedHosts=("ASSETS.example.com",))
    with pytest.raises(ValueError):
        GetPolicy(AllowedPathPrefixes=("relative",))
    with pytest.raises(ValueError):
        GetPolicy(AllowedHosts=["assets.example.com"])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        GetPolicy(AllowQuery="yes")  # type: ignore[arg-type]

    AssertDenied(
        "NETWORK_DNS_TIMEOUT",
        lambda: BoundedDnsResolver().Resolve("assets.example.com", 443, 0),
    )


def test_tls_context_requires_certificate_and_hostname_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "secrets.log"))

    Context = _BuildTlsContext()

    assert Context.verify_mode == ssl.CERT_REQUIRED
    assert Context.check_hostname is True
    assert Context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert Context.keylog_filename is None
    assert not (tmp_path / "secrets.log").exists()


def test_pinned_connection_uses_one_aggregate_connect_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    Target = ResolveNetworkTarget(
        "https://assets.example.com/models/hero.zip",
        GetPolicy(),
        FakeResolver(("93.184.216.34", "93.184.216.35")),
        1.0,
    )
    Times = iter((0.0, 0.0, 3.0))
    Calls: list[float] = []

    def FailConnect(Address: tuple[str, int], timeout: float) -> None:
        del Address
        Calls.append(timeout)
        raise TimeoutError

    monkeypatch.setattr("socket.create_connection", FailConnect)
    Connection = _PinnedHttpsConnection(Target, 2.0, lambda: next(Times))

    AssertDenied("NETWORK_CONNECT_FAILED", Connection.connect)
    assert Calls == [2.0]
