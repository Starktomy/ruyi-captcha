import socket
import socketserver
import struct
import threading
import unittest

from ruyi_captcha.proxy import (LocalProxy, ProxyError, ProxySpec, parse_proxy,
                                proxy_for_context)


class ProxyParsingTests(unittest.TestCase):
    def test_formats_and_default_ports(self):
        cases = {
            "http://proxy.test": ("http", "proxy.test", 80, None, None),
            "https://proxy.test:8443": ("https", "proxy.test", 8443, None, None),
            "socks5://proxy.test": ("socks5", "proxy.test", 1080, None, None),
            "socks5h://us%40er:p%3Ass@proxy.test:7878": ("socks5h", "proxy.test", 7878,
                                                           "us@er", "p:ss"),
        }
        for value, expected in cases.items():
            spec = parse_proxy(value)
            with self.subTest(value=value):
                self.assertEqual((spec.scheme, spec.host, spec.port,
                                  spec.username, spec.password), expected)

    def test_invalid_values_do_not_echo_credentials(self):
        for value in (123, "", "ftp://proxy.test:1", "http://proxy.test/path",
                      "http://proxy.test?x=1", "http://proxy.test#frag",
                      "socks5://proxy.test:bad", "http://:pass@proxy.test",
                      "http://user:pass@proxy.test\x00"):
            with self.subTest(value=value), self.assertRaises(ProxyError) as caught:
                parse_proxy(value)
            self.assertNotIn("pass", str(caught.exception))

    def test_http_proxy_is_passed_through_unchanged(self):
        with proxy_for_context("http://user:pass@proxy.test:8000") as value:
            self.assertEqual(value, "http://user:pass@proxy.test:8000")

    def test_socks_proxy_opens_and_closes_a_loopback_facade(self):
        with proxy_for_context("socks5h://proxy.test:1080") as value:
            self.assertRegex(value, r"^http://127\.0\.0\.1:\d+$")
            host, port = value.rsplit(":", 1)
            self.assertEqual(host, "http://127.0.0.1")
            probe = socket.socket()
            probe.settimeout(2)
            probe.connect(("127.0.0.1", int(port)))
            probe.close()
        # The facade is gone after the context manager exits.
        sock = socket.socket()
        sock.settimeout(2)
        with self.assertRaises(OSError):
            sock.connect(("127.0.0.1", int(port)))
        sock.close()


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class _SocksHandler(socketserver.BaseRequestHandler):
    echo_port = None
    expected_user = None
    expected_password = None
    seen_host = None

    def _read(self, size):
        data = b""
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                raise OSError("short read")
            data += chunk
        return data

    def handle(self):
        version, count = self._read(2)
        methods = self._read(count)
        if self.expected_user is not None:
            assert 2 in methods
            self.request.sendall(b"\x05\x02")
            version, ulen = self._read(2)
            user = self._read(ulen).decode()
            (plen,) = struct.unpack("!B", self._read(1))
            password = self._read(plen).decode()
            assert (user, password) == (self.expected_user, self.expected_password)
            self.request.sendall(b"\x01\x00")
        else:
            self.request.sendall(b"\x05\x00")
        version, command, reserved, atyp = self._read(4)
        if atyp == 3:
            (length,) = struct.unpack("!B", self._read(1))
            host = self._read(length).decode()
        elif atyp == 1:
            host = socket.inet_ntoa(self._read(4))
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, self._read(16))
        else:
            raise OSError("bad atyp")
        (port,) = struct.unpack("!H", self._read(2))
        type(self).seen_host = host
        upstream = socket.create_connection(("127.0.0.1", self.echo_port), timeout=5)
        self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01" +
                             struct.pack("!H", self.echo_port))
        try:
            while True:
                readable = [self.request, upstream]
                import select
                ready, _, _ = select.select(readable, [], [], 5)
                if not ready:
                    return
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.request else self.request).sendall(data)
        finally:
            upstream.close()


class ProxyTunnelTests(unittest.TestCase):
    def test_socks5h_tunnel_with_authentication(self):
        echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
        echo.daemon_threads = True
        echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
        echo_thread.start()
        _SocksHandler.echo_port = echo.server_address[1]
        _SocksHandler.expected_user = "user"
        _SocksHandler.expected_password = "pass"
        _SocksHandler.seen_host = None
        socks = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SocksHandler)
        socks.daemon_threads = True
        socks_thread = threading.Thread(target=socks.serve_forever, daemon=True)
        socks_thread.start()
        bridge = LocalProxy(ProxySpec("socks5h", "127.0.0.1",
                                      socks.server_address[1], "user", "pass"))
        try:
            port = int(bridge.url.rsplit(":", 1)[1])
            client = socket.create_connection(("127.0.0.1", port), timeout=5)
            client.sendall(b"CONNECT echo.test:443 HTTP/1.1\r\n"
                           b"Host: echo.test:443\r\n\r\n")
            response = client.recv(4096)
            self.assertIn(b"200 Connection Established", response)
            client.sendall(b"hello-through-socks")
            self.assertEqual(client.recv(64), b"hello-through-socks")
            self.assertEqual(_SocksHandler.seen_host, "echo.test")
            client.close()
        finally:
            bridge.close()
            socks.shutdown()
            socks.server_close()
            echo.shutdown()
            echo.server_close()
            socks_thread.join(timeout=2)
            echo_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
