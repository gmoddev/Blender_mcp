"""Purpose-scoped HTTPS and bounded artifact download boundary."""

from __future__ import annotations

import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from .archive_boundary import ArtifactStore, ManagedArtifactDirectory
from .filesystem_boundary import WindowsReservedNames


class NetworkBoundaryError(ValueError):
    """Structured, redacted network-boundary failure."""

    def __init__(self, Code: str, PublicMessage: str):
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage


@dataclass(frozen=True)
class NetworkLimits:
    MaxUrlCharacters: int = 2048
    MaxDnsAddresses: int = 16
    MaxRedirects: int = 3
    MaxResponseBytes: int = 256 * 1024 * 1024
    MaxHeaderCount: int = 64
    MaxHeaderBytes: int = 32 * 1024
    ChunkBytes: int = 1024 * 1024
    DnsSeconds: float = 5.0
    ConnectSeconds: float = 10.0
    ReadSeconds: float = 30.0
    TotalSeconds: float = 120.0

    def __post_init__(self) -> None:
        IntegerLimits = (
            self.MaxUrlCharacters,
            self.MaxDnsAddresses,
            self.MaxRedirects,
            self.MaxResponseBytes,
            self.MaxHeaderCount,
            self.MaxHeaderBytes,
            self.ChunkBytes,
        )
        if any(
            not isinstance(Value, int) or isinstance(Value, bool) or Value < 0
            for Value in IntegerLimits
        ):
            raise ValueError("Network integer limits must be non-negative integers")
        if any(
            Value <= 0
            for Value in (
                self.MaxUrlCharacters,
                self.MaxDnsAddresses,
                self.MaxResponseBytes,
                self.MaxHeaderCount,
                self.MaxHeaderBytes,
                self.ChunkBytes,
            )
        ):
            raise ValueError("Network size limits must be positive")
        TimeLimits = (self.DnsSeconds, self.ConnectSeconds, self.ReadSeconds, self.TotalSeconds)
        if any(
            not isinstance(Value, (int, float)) or isinstance(Value, bool) or not 0 < Value <= 3600
            for Value in TimeLimits
        ):
            raise ValueError("Network deadlines must be positive and bounded")


@dataclass(frozen=True)
class HttpPurposePolicy:
    """One explicit HTTPS destination and response contract."""

    Name: str
    AllowedHosts: tuple[str, ...]
    AllowedPathPrefixes: tuple[str, ...]
    AllowedPorts: tuple[int, ...] = (443,)
    AllowedContentTypes: tuple[str, ...] = ()
    AllowQuery: bool = True
    Limits: NetworkLimits = NetworkLimits()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.Name, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", self.Name) is None
        ):
            raise ValueError("A bounded HTTP purpose name is required")
        if not isinstance(self.AllowedHosts, tuple):
            raise ValueError("HTTP policy hosts must be an immutable tuple")
        if not isinstance(self.AllowedPathPrefixes, tuple):
            raise ValueError("HTTP policy paths must be an immutable tuple")
        if not isinstance(self.AllowedPorts, tuple):
            raise ValueError("HTTP policy ports must be an immutable tuple")
        if not isinstance(self.AllowedContentTypes, tuple):
            raise ValueError("HTTP policy content types must be an immutable tuple")
        if not isinstance(self.AllowQuery, bool):
            raise ValueError("HTTP policy query control must be a boolean")
        if not isinstance(self.Limits, NetworkLimits):
            raise ValueError("HTTP policy limits must use NetworkLimits")
        if not self.AllowedHosts or not self.AllowedPathPrefixes or not self.AllowedPorts:
            raise ValueError("HTTP purpose policies require hosts, paths, and ports")
        for Host in self.AllowedHosts:
            if _NormalizeHostname(Host) != Host:
                raise ValueError("HTTP policy hosts must be canonical lower-case DNS names")
        for Prefix in self.AllowedPathPrefixes:
            _ValidatePolicyPathPrefix(Prefix)
        if any(
            not isinstance(Port, int) or isinstance(Port, bool) or not 1 <= Port <= 65535
            for Port in self.AllowedPorts
        ):
            raise ValueError("HTTP policy ports must be valid integers")
        for ContentType in self.AllowedContentTypes:
            if (
                not isinstance(ContentType, str)
                or not ContentType
                or ContentType != ContentType.lower()
                or ";" in ContentType
            ):
                raise ValueError("HTTP content types must be canonical media types")


@dataclass(frozen=True)
class AuthorizedUrl:
    CanonicalUrl: str
    Host: str
    Port: int
    RequestTarget: str


@dataclass(frozen=True)
class NetworkTarget:
    Url: AuthorizedUrl
    Addresses: tuple[str, ...]


@dataclass(frozen=True)
class HttpDownloadResult:
    Workspace: ManagedArtifactDirectory
    RelativePath: str
    FinalUrl: str
    BytesWritten: int
    ContentType: str | None


class DnsResolverProtocol(Protocol):
    def Resolve(self, Host: str, Port: int, TimeoutSeconds: float) -> tuple[str, ...]: ...


class HttpResponseProtocol(Protocol):
    status: int

    def getheaders(self) -> list[tuple[str, str]]: ...

    def read(self, Amount: int | None = None) -> bytes: ...


class HttpConnectionProtocol(Protocol):
    sock: socket.socket | ssl.SSLSocket | None

    def request(
        self,
        Method: str,
        Url: str,
        Body: bytes | None = None,
        Headers: Mapping[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> HttpResponseProtocol: ...

    def close(self) -> None: ...


LookupFunction = Callable[[str, int], Sequence[str]]
ConnectionFactory = Callable[[NetworkTarget, float], HttpConnectionProtocol]
ClockFunction = Callable[[], float]


class BoundedDnsResolver:
    """Bound outstanding system resolver calls even when the OS resolver stalls."""

    def __init__(self, MaxOutstanding: int = 4, Lookup: LookupFunction | None = None):
        if (
            not isinstance(MaxOutstanding, int)
            or isinstance(MaxOutstanding, bool)
            or MaxOutstanding <= 0
        ):
            raise ValueError("DNS resolver capacity must be a positive integer")
        self._Capacity = threading.BoundedSemaphore(MaxOutstanding)
        self._Lookup = Lookup or _SystemLookup

    def Resolve(self, Host: str, Port: int, TimeoutSeconds: float) -> tuple[str, ...]:
        if (
            not isinstance(TimeoutSeconds, (int, float))
            or isinstance(TimeoutSeconds, bool)
            or not 0 < TimeoutSeconds <= 3600
        ):
            raise _Deny("NETWORK_DNS_TIMEOUT", "DNS resolution deadline is invalid")
        if not self._Capacity.acquire(blocking=False):
            raise _Deny("NETWORK_DNS_CAPACITY_EXCEEDED", "DNS resolution capacity is exhausted")
        Results: queue.Queue[tuple[tuple[str, ...] | None, Exception | None]] = queue.Queue(
            maxsize=1
        )

        def ResolveWorker() -> None:
            Result: tuple[tuple[str, ...] | None, Exception | None]
            try:
                Value = tuple(self._Lookup(Host, Port))
                Result = (Value, None)
            except Exception as Error:
                Result = (None, Error)
            finally:
                self._Capacity.release()
            try:
                Results.put_nowait(Result)
            except queue.Full:
                pass

        Worker = threading.Thread(
            target=ResolveWorker,
            name="BlenderMCP-DNS",
            daemon=True,
        )
        Worker.start()
        try:
            Addresses, Error = Results.get(timeout=TimeoutSeconds)
        except queue.Empty as Error:
            raise _Deny("NETWORK_DNS_TIMEOUT", "DNS resolution exceeded its deadline") from Error
        if Error is not None:
            raise _Deny("NETWORK_DNS_FAILED", "DNS resolution failed") from Error
        if not Addresses:
            raise _Deny("NETWORK_DNS_FAILED", "DNS resolution returned no addresses")
        return Addresses


class BoundedHttpClient:
    """Direct HTTPS GET client with manual redirect and streamed-body limits."""

    def __init__(
        self,
        Policy: HttpPurposePolicy,
        Resolver: DnsResolverProtocol | None = None,
        ConnectionBuilder: ConnectionFactory | None = None,
        Clock: ClockFunction | None = None,
    ) -> None:
        self.Policy = Policy
        self.Resolver = Resolver or BoundedDnsResolver()
        self.ConnectionBuilder = ConnectionBuilder or _BuildPinnedHttpsConnection
        self.Clock = Clock or time.monotonic

    def Download(
        self,
        Url: str,
        Store: ArtifactStore,
        RequestId: str,
        FileName: str,
    ) -> HttpDownloadResult:
        SafeFileName = _ValidateArtifactFileName(FileName)
        Deadline = self.Clock() + self.Policy.Limits.TotalSeconds
        CurrentUrl = Url
        Redirects = 0
        Workspace: ManagedArtifactDirectory | None = None
        try:
            while True:
                Target = ResolveNetworkTarget(
                    CurrentUrl,
                    self.Policy,
                    self.Resolver,
                    _RemainingSeconds(Deadline, self.Clock, self.Policy.Limits.DnsSeconds),
                )
                Connection = self.ConnectionBuilder(
                    Target,
                    _RemainingSeconds(
                        Deadline,
                        self.Clock,
                        self.Policy.Limits.ConnectSeconds,
                    ),
                )
                try:
                    Connection.request(
                        "GET",
                        Target.Url.RequestTarget,
                        Body=None,
                        Headers={
                            "Accept": "application/octet-stream",
                            "Connection": "close",
                            "Host": _GetHostHeader(Target.Url),
                            "User-Agent": "gmoddev-BlenderMCP/1.0",
                        },
                    )
                    Response = Connection.getresponse()
                    Headers = _ValidateHeaders(Response.getheaders(), self.Policy.Limits)
                    if Response.status in (301, 302, 303, 307, 308):
                        if Redirects >= self.Policy.Limits.MaxRedirects:
                            raise _Deny(
                                "NETWORK_REDIRECT_LIMIT_EXCEEDED",
                                "The HTTPS response exceeded its redirect limit",
                            )
                        Locations = Headers.get("location", ())
                        if len(Locations) != 1:
                            raise _Deny(
                                "NETWORK_REDIRECT_INVALID",
                                "The HTTPS redirect did not provide one valid destination",
                            )
                        CurrentUrl = urljoin(Target.Url.CanonicalUrl, Locations[0])
                        Redirects += 1
                        continue
                    if Response.status != 200:
                        raise _Deny(
                            "NETWORK_HTTP_STATUS_DENIED",
                            "The HTTPS response status is not accepted for this purpose",
                        )
                    ContentType = _ValidateContentType(Headers, self.Policy)
                    ExpectedBytes = _ValidateBodyHeaders(Headers, self.Policy.Limits)
                    Workspace = Store.CreateWorkspace(RequestId)
                    OutputPath = Workspace.Root / SafeFileName
                    BytesWritten = self._StreamBody(
                        Response,
                        Connection,
                        OutputPath,
                        Deadline,
                        ExpectedBytes,
                    )
                    Workspace.RelativeFiles = (SafeFileName,)
                    return HttpDownloadResult(
                        Workspace=Workspace,
                        RelativePath=SafeFileName,
                        FinalUrl=Target.Url.CanonicalUrl,
                        BytesWritten=BytesWritten,
                        ContentType=ContentType,
                    )
                finally:
                    Connection.close()
        except NetworkBoundaryError:
            if Workspace is not None:
                Workspace.Cleanup()
            raise
        except Exception as Error:
            if Workspace is not None:
                Workspace.Cleanup()
            raise _Deny("NETWORK_REQUEST_FAILED", "The HTTPS request failed safely") from Error

    def _StreamBody(
        self,
        Response: HttpResponseProtocol,
        Connection: HttpConnectionProtocol,
        OutputPath: Path,
        Deadline: float,
        ExpectedBytes: int | None,
    ) -> int:
        Total = 0
        with OutputPath.open("xb") as Destination:
            while True:
                ReadTimeout = _RemainingSeconds(
                    Deadline,
                    self.Clock,
                    self.Policy.Limits.ReadSeconds,
                )
                if Connection.sock is not None:
                    Connection.sock.settimeout(ReadTimeout)
                Chunk = Response.read(self.Policy.Limits.ChunkBytes)
                if not Chunk:
                    break
                Total += len(Chunk)
                if Total > self.Policy.Limits.MaxResponseBytes:
                    raise _Deny(
                        "NETWORK_RESPONSE_LIMIT_EXCEEDED",
                        "The HTTPS response exceeded its byte limit",
                    )
                Destination.write(Chunk)
        if ExpectedBytes is not None and Total != ExpectedBytes:
            raise _Deny(
                "NETWORK_RESPONSE_LENGTH_MISMATCH",
                "The HTTPS response did not match its declared length",
            )
        return Total


def AuthorizeUrl(Url: str, Policy: HttpPurposePolicy) -> AuthorizedUrl:
    """Authorize an HTTPS URL against an exact purpose policy before DNS."""
    if not isinstance(Url, str) or not Url or len(Url) > Policy.Limits.MaxUrlCharacters:
        raise _Deny("NETWORK_URL_DENIED", "The URL is not allowed for this purpose")
    if any(Character.isspace() or ord(Character) < 32 for Character in Url):
        raise _Deny("NETWORK_URL_DENIED", "The URL is not allowed for this purpose")
    try:
        Parsed = urlsplit(Url)
        Port = Parsed.port or 443
    except ValueError as Error:
        raise _Deny("NETWORK_URL_DENIED", "The URL is not allowed for this purpose") from Error
    Host = _ValidateParsedUrl(Parsed, Policy, Port)
    PathValue = Parsed.path or "/"
    RequestTarget = PathValue + (f"?{Parsed.query}" if Parsed.query else "")
    Authority = Host if Port == 443 else f"{Host}:{Port}"
    return AuthorizedUrl(
        CanonicalUrl=urlunsplit(("https", Authority, PathValue, Parsed.query, "")),
        Host=Host,
        Port=Port,
        RequestTarget=RequestTarget,
    )


def ResolveNetworkTarget(
    Url: str,
    Policy: HttpPurposePolicy,
    Resolver: DnsResolverProtocol,
    TimeoutSeconds: float,
) -> NetworkTarget:
    """Authorize a URL and require every DNS answer to be globally routable."""
    Authorized = AuthorizeUrl(Url, Policy)
    RawAddresses = Resolver.Resolve(Authorized.Host, Authorized.Port, TimeoutSeconds)
    if len(RawAddresses) > Policy.Limits.MaxDnsAddresses:
        raise _Deny("NETWORK_DNS_ADDRESS_LIMIT_EXCEEDED", "DNS returned too many addresses")
    Addresses: list[str] = []
    for RawAddress in RawAddresses:
        Address = _RequireGlobalAddress(RawAddress)
        if Address not in Addresses:
            Addresses.append(Address)
    if not Addresses:
        raise _Deny("NETWORK_DNS_FAILED", "DNS resolution returned no addresses")
    return NetworkTarget(Authorized, tuple(Addresses))


def RequireConnectedPeer(PeerAddress: str, AuthorizedAddresses: Sequence[str]) -> str:
    """Verify that the socket peer is one of the authorized public DNS answers."""
    Peer = _RequireGlobalAddress(PeerAddress)
    Expected = {_RequireGlobalAddress(Address) for Address in AuthorizedAddresses}
    if Peer not in Expected:
        raise _Deny(
            "NETWORK_CONNECTED_PEER_MISMATCH",
            "The connected peer did not match the authorized DNS result",
        )
    return Peer


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        Target: NetworkTarget,
        TimeoutSeconds: float,
        Clock: ClockFunction = time.monotonic,
    ):
        Context = _BuildTlsContext()
        self._TlsContext = Context
        super().__init__(
            Target.Url.Host,
            Target.Url.Port,
            timeout=TimeoutSeconds,
            context=Context,
        )
        self._Target = Target
        self._ConnectDeadline = Clock() + TimeoutSeconds
        self._Clock = Clock

    def connect(self) -> None:
        LastError: Exception | None = None
        for Address in self._Target.Addresses:
            RemainingSeconds = self._ConnectDeadline - self._Clock()
            if RemainingSeconds <= 0:
                break
            RawSocket: socket.socket | None = None
            try:
                RawSocket = socket.create_connection(
                    (Address, self._Target.Url.Port),
                    timeout=RemainingSeconds,
                )
                PeerAddress = cast(tuple[str, int], RawSocket.getpeername())[0]
                RequireConnectedPeer(PeerAddress, self._Target.Addresses)
                WrappedSocket = self._TlsContext.wrap_socket(
                    RawSocket,
                    server_hostname=self._Target.Url.Host,
                )
                RequireConnectedPeer(
                    cast(tuple[str, int], WrappedSocket.getpeername())[0],
                    self._Target.Addresses,
                )
                self.sock = WrappedSocket
                return
            except (OSError, ssl.SSLError, NetworkBoundaryError) as Error:
                LastError = Error
                if RawSocket is not None:
                    RawSocket.close()
        raise _Deny(
            "NETWORK_CONNECT_FAILED", "The authorized HTTPS endpoint could not be reached"
        ) from LastError


def _BuildPinnedHttpsConnection(
    Target: NetworkTarget, TimeoutSeconds: float
) -> HttpConnectionProtocol:
    return cast(HttpConnectionProtocol, _PinnedHttpsConnection(Target, TimeoutSeconds))


def _BuildTlsContext() -> ssl.SSLContext:
    """Build a verified client context without environment-enabled TLS key logging."""
    Context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    Context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    Context.minimum_version = ssl.TLSVersion.TLSv1_2
    Context.set_alpn_protocols(["http/1.1"])
    StrictFlag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if StrictFlag:
        Context.verify_flags |= StrictFlag
    return Context


def _SystemLookup(Host: str, Port: int) -> Sequence[str]:
    Results = socket.getaddrinfo(
        Host,
        Port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(Result[4][0]) for Result in Results)


def _ValidateParsedUrl(Parsed: SplitResult, Policy: HttpPurposePolicy, Port: int) -> str:
    if (
        Parsed.scheme != "https"
        or not Parsed.netloc
        or Parsed.username is not None
        or Parsed.password is not None
        or Parsed.fragment
        or Parsed.hostname is None
        or "\\" in Parsed.netloc
    ):
        raise _Deny("NETWORK_URL_DENIED", "The URL is not allowed for this purpose")
    Host = _NormalizeHostname(Parsed.hostname)
    if Host not in Policy.AllowedHosts or Port not in Policy.AllowedPorts:
        raise _Deny("NETWORK_DESTINATION_DENIED", "The destination is not allowed for this purpose")
    PathValue = Parsed.path or "/"
    PathComponents = () if PathValue == "/" else tuple(PathValue[1:].split("/"))
    if (
        "\\" in PathValue
        or "%" in PathValue
        or PathValue.startswith("//")
        or any(Component in ("", ".", "..") for Component in PathComponents)
    ):
        raise _Deny("NETWORK_PATH_DENIED", "The URL path is not allowed for this purpose")
    if not any(_PathHasPrefix(PathValue, Prefix) for Prefix in Policy.AllowedPathPrefixes):
        raise _Deny("NETWORK_PATH_DENIED", "The URL path is not allowed for this purpose")
    if Parsed.query and not Policy.AllowQuery:
        raise _Deny("NETWORK_QUERY_DENIED", "URL queries are not allowed for this purpose")
    return Host


def _NormalizeHostname(Host: str) -> str:
    if not isinstance(Host, str) or not Host or Host.endswith(".") or "_" in Host:
        raise _Deny("NETWORK_HOST_DENIED", "The network host is invalid")
    try:
        ipaddress.ip_address(Host)
    except ValueError:
        pass
    else:
        raise _Deny("NETWORK_HOST_DENIED", "Literal network addresses are not accepted")
    try:
        Canonical = Host.encode("idna").decode("ascii").lower()
    except UnicodeError as Error:
        raise _Deny("NETWORK_HOST_DENIED", "The network host is invalid") from Error
    Labels = Canonical.split(".")
    if (
        len(Canonical) > 253
        or len(Labels) < 2
        or any(
            not Label
            or len(Label) > 63
            or Label.startswith("-")
            or Label.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", Label) is None
            for Label in Labels
        )
    ):
        raise _Deny("NETWORK_HOST_DENIED", "The network host is invalid")
    return Canonical


def _ValidatePolicyPathPrefix(Prefix: str) -> None:
    PrefixComponents = () if Prefix == "/" else tuple(Prefix[1:].split("/"))
    if (
        not isinstance(Prefix, str)
        or not Prefix.startswith("/")
        or "\\" in Prefix
        or "%" in Prefix
        or "?" in Prefix
        or "#" in Prefix
        or Prefix.startswith("//")
        or (Prefix != "/" and Prefix.endswith("/"))
        or any(Component in ("", ".", "..") for Component in PrefixComponents)
    ):
        raise ValueError("HTTP policy path prefixes must be canonical absolute paths")


def _PathHasPrefix(PathValue: str, Prefix: str) -> bool:
    return Prefix == "/" or PathValue == Prefix or PathValue.startswith(f"{Prefix}/")


def _RequireGlobalAddress(AddressText: str) -> str:
    try:
        Address = ipaddress.ip_address(AddressText)
    except ValueError as Error:
        raise _Deny("NETWORK_DNS_ADDRESS_DENIED", "DNS returned an invalid address") from Error
    if isinstance(Address, ipaddress.IPv6Address) and Address.ipv4_mapped is not None:
        Address = Address.ipv4_mapped
    if (
        not Address.is_global
        or Address.is_private
        or Address.is_loopback
        or Address.is_link_local
        or Address.is_multicast
        or Address.is_reserved
        or Address.is_unspecified
    ):
        raise _Deny("NETWORK_DNS_ADDRESS_DENIED", "DNS returned a non-public address")
    return Address.compressed


def _ValidateHeaders(
    HeaderPairs: Sequence[tuple[str, str]], Limits: NetworkLimits
) -> dict[str, tuple[str, ...]]:
    if len(HeaderPairs) > Limits.MaxHeaderCount:
        raise _Deny("NETWORK_HEADER_LIMIT_EXCEEDED", "The HTTPS response has too many headers")
    TotalBytes = 0
    Headers: dict[str, list[str]] = {}
    for Name, Value in HeaderPairs:
        if (
            re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", Name) is None
            or "\r" in Value
            or "\n" in Value
            or any(ord(Character) < 32 and Character != "\t" for Character in Value)
        ):
            raise _Deny("NETWORK_HEADER_INVALID", "The HTTPS response contains an invalid header")
        TotalBytes += len(Name.encode("ascii")) + len(Value.encode("latin-1", errors="replace")) + 4
        if TotalBytes > Limits.MaxHeaderBytes:
            raise _Deny("NETWORK_HEADER_LIMIT_EXCEEDED", "The HTTPS response headers are too large")
        Headers.setdefault(Name.lower(), []).append(Value.strip())
    return {Name: tuple(Values) for Name, Values in Headers.items()}


def _ValidateContentType(
    Headers: Mapping[str, tuple[str, ...]], Policy: HttpPurposePolicy
) -> str | None:
    Values = Headers.get("content-type", ())
    if len(Values) > 1:
        raise _Deny("NETWORK_CONTENT_TYPE_DENIED", "The HTTPS response content type is invalid")
    ContentType = Values[0].split(";", 1)[0].strip().lower() if Values else None
    if Policy.AllowedContentTypes and ContentType not in Policy.AllowedContentTypes:
        raise _Deny("NETWORK_CONTENT_TYPE_DENIED", "The HTTPS response content type is not allowed")
    return ContentType


def _ValidateBodyHeaders(
    Headers: Mapping[str, tuple[str, ...]], Limits: NetworkLimits
) -> int | None:
    Encodings = Headers.get("content-encoding", ())
    if Encodings and any(Value.lower() != "identity" for Value in Encodings):
        raise _Deny(
            "NETWORK_CONTENT_ENCODING_DENIED", "Encoded HTTPS response bodies are not accepted"
        )
    Lengths = Headers.get("content-length", ())
    TransferEncodings = Headers.get("transfer-encoding", ())
    if Lengths and TransferEncodings:
        raise _Deny("NETWORK_BODY_FRAMING_DENIED", "The HTTPS response body framing is ambiguous")
    if TransferEncodings and (
        len(TransferEncodings) != 1 or TransferEncodings[0].lower() != "chunked"
    ):
        raise _Deny(
            "NETWORK_BODY_FRAMING_DENIED", "The HTTPS response transfer encoding is unsupported"
        )
    if Lengths:
        if any(re.fullmatch(r"[0-9]+", Value) is None for Value in Lengths):
            raise _Deny("NETWORK_BODY_FRAMING_DENIED", "The HTTPS response length is invalid")
        try:
            ParsedLengths = {int(Value, 10) for Value in Lengths}
        except ValueError as Error:
            raise _Deny(
                "NETWORK_BODY_FRAMING_DENIED", "The HTTPS response length is invalid"
            ) from Error
        if len(ParsedLengths) != 1 or next(iter(ParsedLengths)) < 0:
            raise _Deny("NETWORK_BODY_FRAMING_DENIED", "The HTTPS response length is ambiguous")
        if next(iter(ParsedLengths)) > Limits.MaxResponseBytes:
            raise _Deny(
                "NETWORK_RESPONSE_LIMIT_EXCEEDED", "The HTTPS response exceeded its byte limit"
            )
        return next(iter(ParsedLengths))
    return None


def _ValidateArtifactFileName(FileName: str) -> str:
    if (
        not isinstance(FileName, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", FileName) is None
        or FileName.endswith((" ", "."))
        or FileName.split(".", 1)[0].upper() in WindowsReservedNames
    ):
        raise _Deny("NETWORK_ARTIFACT_NAME_DENIED", "The artifact filename is invalid")
    return FileName


def _GetHostHeader(Url: AuthorizedUrl) -> str:
    return Url.Host if Url.Port == 443 else f"{Url.Host}:{Url.Port}"


def _RemainingSeconds(Deadline: float, Clock: ClockFunction, PhaseLimit: float) -> float:
    Remaining = Deadline - Clock()
    if Remaining <= 0:
        raise _Deny("NETWORK_TOTAL_TIMEOUT", "The HTTPS request exceeded its total deadline")
    return min(Remaining, PhaseLimit)


def _Deny(Code: str, PublicMessage: str) -> NetworkBoundaryError:
    return NetworkBoundaryError(Code, PublicMessage)
