"""Proxy URL validation and a local HTTP CONNECT bridge for SOCKS5.

The ff-ruyijs Necko transport accepts an HTTP(S) proxy URL. This module keeps
that kernel boundary: ``http``/``https`` URLs are passed through unchanged,
while ``socks5``/``socks5h`` URLs are exposed to the kernel through a
short-lived loopback HTTP CONNECT server. The bridge never listens on a public
interface and is closed with the solver Context.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import select
import socket
import socketserver
import struct
import threading
from urllib.parse import unquote, urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}
_MAX_HEADER = 65536


class ProxyError(ValueError):
    """The proxy URL or handshake is invalid."""


@dataclass(frozen=True)
class ProxySpec:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None


def parse_proxy(value: str | None) -> ProxySpec | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProxyError("proxy must be a URL string or None")
    if not value or any(ord(c) < 32 for c in value):
        raise ProxyError("proxy must be a nonempty URL without control characters")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ProxyError("proxy must use http, https, socks5, or socks5h")
    if not parsed.hostname:
        raise ProxyError("proxy must include a hostname")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProxyError("proxy URL must not contain a path, query, or fragment")
    try:
        port = parsed.port or _DEFAULT_PORTS[scheme]
    except ValueError as error:
        raise ProxyError("proxy port is invalid") from error
    if not 0 < port < 65536:
        raise ProxyError("proxy port is invalid")
    username = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else None
    if username is not None and not username:
        raise ProxyError("proxy username must not be empty")
    return ProxySpec(scheme, parsed.hostname, port, username, password)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ProxyError("proxy closed the connection")
        data += chunk
    return data


def _socks_connect(spec: ProxySpec, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((spec.host, spec.port), timeout=30)
    try:
        methods = b"\x00"
        if spec.username is not None:
            methods = b"\x00\x02"
        sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
        version, method = _recv_exact(sock, 2)
        if version != 5:
            raise ProxyError("upstream is not a SOCKS5 proxy")
        if method == 2:
            if spec.username is None:
                raise ProxyError("SOCKS5 proxy requires authentication")
            user = spec.username.encode("utf-8")
            password = (spec.password or "").encode("utf-8")
            if len(user) > 255 or len(password) > 255:
                raise ProxyError("SOCKS5 credentials are too long")
            sock.sendall(b"\x01" + bytes([len(user)]) + user +
                         bytes([len(password)]) + password)
            auth_version, status = _recv_exact(sock, 2)
            if auth_version != 1 or status != 0:
                raise ProxyError("SOCKS5 authentication failed")
        elif method != 0:
            raise ProxyError("SOCKS5 proxy did not accept the offered auth method")

        if spec.scheme == "socks5h":
            try:
                encoded = host.encode("idna")
            except UnicodeError as error:
                raise ProxyError("SOCKS5 target hostname is invalid") from error
            if not encoded or len(encoded) > 255:
                raise ProxyError("SOCKS5 target hostname is too long")
            address = b"\x03" + bytes([len(encoded)]) + encoded
        else:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not infos:
                raise ProxyError("SOCKS5 target hostname did not resolve")
            family, _, _, _, sockaddr = infos[0]
            if family == socket.AF_INET6:
                address = b"\x04" + socket.inet_pton(socket.AF_INET6, sockaddr[0])
            else:
                address = b"\x01" + socket.inet_pton(socket.AF_INET, sockaddr[0])
        sock.sendall(b"\x05\x01\x00" + address + struct.pack("!H", port))
        version, reply, reserved, atyp = _recv_exact(sock, 4)
        if version != 5 or reply != 0:
            raise ProxyError("SOCKS5 CONNECT failed")
        if atyp == 1:
            _recv_exact(sock, 4)
        elif atyp == 4:
            _recv_exact(sock, 16)
        elif atyp == 3:
            _recv_exact(sock, _recv_exact(sock, 1)[0])
        else:
            raise ProxyError("SOCKS5 CONNECT reply has an invalid address type")
        _recv_exact(sock, 2)
        return sock
    except BaseException:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _http_connect(spec: ProxySpec, host: str, port: int) -> socket.socket:
    sock = socket.create_connection((spec.host, spec.port), timeout=30)
    try:
        lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
        if spec.username is not None:
            import base64
            token = base64.b64encode(
                (spec.username + ":" + (spec.password or "")).encode()).decode()
            lines.append("Proxy-Authorization: Basic " + token)
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        data = b""
        while b"\r\n\r\n" not in data and len(data) < _MAX_HEADER:
            chunk = sock.recv(4096)
            if not chunk:
                raise ProxyError("HTTP proxy closed during CONNECT")
            data += chunk
        status = data.split(b"\r\n", 1)[0].split(b" ")
        if len(status) < 2 or status[1] != b"200":
            raise ProxyError("HTTP proxy CONNECT failed")
        return sock
    except BaseException:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _connect(spec: ProxySpec, host: str, port: int) -> socket.socket:
    if spec.scheme in ("socks5", "socks5h"):
        return _socks_connect(spec, host, port)
    return _http_connect(spec, host, port)


def _relay(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [], 60)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    finally:
        for sock in (left, right):
            try:
                sock.close()
            except OSError:
                pass


class _Handler(socketserver.BaseRequestHandler):
    spec: ProxySpec

    def handle(self) -> None:
        try:
            self._handle()
        except (OSError, ProxyError):
            # Peer resets and deadline closes are normal tunnel teardown.
            pass

    def _handle(self) -> None:
        self.request.settimeout(60)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < _MAX_HEADER:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        parts = lines[0].decode("latin-1").split(" ")
        if len(parts) < 3:
            self._error(400)
            return
        method, target, version = parts[0], parts[1], parts[2]
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                key, _, value = line.partition(b":")
                headers[key.strip().lower()] = value.strip()
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            if not host:
                host, port = target, "443"
            try:
                port = int(port)
            except ValueError:
                self._error(400)
                return
            try:
                upstream = _connect(self.spec, host, port)
            except Exception:
                self._error(502)
                return
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            _relay(self.request, upstream)
            return
        # Absolute-form HTTP is used by some Necko paths; connect to the target
        # through the proxy and rewrite the request to origin-form.
        parsed = urlsplit(target)
        if not parsed.hostname:
            self._error(400)
            return
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        lines_out = [f"{method} {path} {version}".encode("latin-1")]
        for key, value in headers.items():
            if key == b"proxy-connection":
                continue
            lines_out.append(key + b": " + value)
        request = b"\r\n".join(lines_out) + b"\r\n\r\n" + rest
        try:
            upstream = _connect(self.spec, host, port)
        except Exception:
            self._error(502)
            return
        upstream.sendall(request)
        _relay(self.request, upstream)

    def _error(self, code: int) -> None:
        reason = {400: "Bad Request", 502: "Bad Gateway"}.get(code, "Error")
        try:
            self.request.sendall(
                f"HTTP/1.1 {code} {reason}\r\nContent-Length: 0\r\n"
                "Connection: close\r\n\r\n".encode("latin-1"))
        except OSError:
            pass


class LocalProxy:
    """A loopback HTTP CONNECT facade for one SOCKS5 upstream."""

    def __init__(self, spec: ProxySpec):
        if spec.scheme not in ("socks5", "socks5h"):
            raise ProxyError("LocalProxy only supports SOCKS5 upstreams")
        self.spec = spec
        self._server = socketserver.ThreadingTCPServer(
            ("127.0.0.1", 0), _Handler, bind_and_activate=False)
        self._server.allow_reuse_address = True
        self._server.daemon_threads = True
        self._server.server_bind()
        self._server.server_activate()
        self._server.RequestHandlerClass.spec = spec
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1}, daemon=True, name="ruyi-px-proxy")
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
        self._thread.join(timeout=2)


@contextmanager
def proxy_for_context(proxy: str | None):
    """Yield a kernel-compatible proxy URL for the duration of a context.

    HTTP(S) proxies are returned unchanged. SOCKS5 upstreams are exposed
    through a loopback HTTP CONNECT server that is closed on exit.
    """
    spec = parse_proxy(proxy)
    if spec is None or spec.scheme in ("http", "https"):
        yield proxy or None
        return
    bridge = LocalProxy(spec)
    try:
        yield bridge.url
    finally:
        bridge.close()


@contextmanager
def proxy_context(factory, proxy: str | None, **kwargs):
    """Open a solver Context with SOCKS5 support hidden behind a HTTP facade."""
    with proxy_for_context(proxy) as configured:
        with factory(proxy=configured, **kwargs) as context:
            yield context
