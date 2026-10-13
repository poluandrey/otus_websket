from __future__ import annotations

import http.client
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from http_server.server import StaticHTTPServer


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        (cls.root / "index.html").write_bytes(b"root index\n")
        (cls.root / "plain.txt").write_bytes(b"plain text\n")
        (cls.root / "text..txt").write_bytes(b"hello")
        (cls.root / "space in name.txt").write_bytes(b"letters and spaces\n")
        (cls.root / "empty").mkdir()
        (cls.root / "dir").mkdir()
        (cls.root / "dir" / "index.html").write_bytes(b"<html>index</html>\n")
        (cls.root / "page.html").write_bytes(b"<html>page</html>\n")
        (cls.root / "style.css").write_bytes(b"body {}\n")
        (cls.root / "app.js").write_bytes(b"const ok = true;\n")
        (cls.root / "large.bin").write_bytes(b"0123456789abcdef" * 131_072)

        cls.server = StaticHTTPServer(("127.0.0.1", 0), cls.root, thread_limit=8)
        cls.host, cls.port = cls.server.server_address
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp_dir.cleanup()

    def request(self, method: str, target: str) -> tuple[http.client.HTTPResponse, bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, target)
        response = connection.getresponse()
        return response, response.read()

    def test_get_file_and_required_headers(self) -> None:
        response, body = self.request("GET", "/plain.txt")

        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"plain text\n")
        self.assertEqual(response.getheader("Content-Length"), str(len(body)))
        self.assertEqual(response.getheader("Content-Type"), "text/plain")
        for name in ("Server", "Date", "Connection"):
            self.assertIsNotNone(response.getheader(name))

    def test_directory_index(self) -> None:
        response, body = self.request("GET", "/dir/")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"<html>index</html>\n")

    def test_directory_without_index_is_forbidden(self) -> None:
        response, _ = self.request("GET", "/empty/")
        self.assertEqual(response.status, 403)

    def test_missing_file_is_not_found(self) -> None:
        response, _ = self.request("GET", "/missing.html")
        self.assertEqual(response.status, 404)

    def test_file_with_trailing_slash_is_not_found(self) -> None:
        response, _ = self.request("GET", "/page.html/")
        self.assertEqual(response.status, 404)

    def test_query_string_is_ignored(self) -> None:
        response, body = self.request("GET", "/page.html?arg=value")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"<html>page</html>\n")

    def test_percent_encoded_names(self) -> None:
        response, body = self.request("GET", "/space%20in%20name.txt")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"letters and spaces\n")

        response, body = self.request("GET", "/%70%61%67%65%2e%68%74%6d%6c")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"<html>page</html>\n")

    def test_filename_with_two_dots_is_allowed(self) -> None:
        response, body = self.request("GET", "/text..txt")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"hello")

    def test_traversal_is_forbidden(self) -> None:
        response, _ = self.request("GET", "/../../../../etc/passwd")
        self.assertEqual(response.status, 403)

        response, _ = self.request("GET", "/%2e%2e/%2e%2e/etc/passwd")
        self.assertEqual(response.status, 403)

    def test_unsupported_method(self) -> None:
        response, _ = self.request("POST", "/page.html")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")

    def test_head_has_get_headers_but_no_body(self) -> None:
        response, body = self.request("HEAD", "/page.html")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Length"), "18")
        self.assertEqual(response.getheader("Content-Type"), "text/html")
        self.assertEqual(body, b"")

    def test_content_types(self) -> None:
        expectations = {
            "/page.html": "text/html",
            "/style.css": "text/css",
            "/app.js": "application/javascript",
        }
        for target, expected in expectations.items():
            with self.subTest(target=target):
                response, _ = self.request("GET", target)
                self.assertEqual(response.getheader("Content-Type"), expected)

    def test_large_file_is_streamed_completely(self) -> None:
        response, body = self.request("GET", "/large.bin")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(body), 2 * 1024 * 1024)
        self.assertEqual(response.getheader("Content-Length"), str(len(body)))

    def test_incomplete_request_does_not_hang_server(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=2) as client:
            client.sendall(b"GET / HTTP/1.1\n")

        response, _ = self.request("GET", "/plain.txt")
        self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
