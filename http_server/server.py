"""Threaded static-file HTTP/1.x server."""

from __future__ import annotations

import email.utils
import logging
import os
import re
import socket
import socketserver
import stat
import threading
from http import HTTPStatus
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote, urlsplit

from .config import ServerConfig


LOGGER = logging.getLogger(__name__)
SERVER_NAME = "otus-httpd/1.0"
MAX_HEADER_SIZE = 64 * 1024
FILE_CHUNK_SIZE = 64 * 1024
REQUEST_TIMEOUT = 5.0
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_HEADER_NAME = re.compile(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

CONTENT_TYPES = {
    ".css": "text/css",
    ".gif": "image/gif",
    ".htm": "text/html",
    ".html": "text/html",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "application/javascript",
    ".png": "image/png",
    ".swf": "application/x-shockwave-flash",
    ".txt": "text/plain",
}


class RequestError(Exception):
    """An HTTP error that can safely be returned to a client."""

    def __init__(self, status: HTTPStatus) -> None:
        super().__init__(status.phrase)
        self.status = status


class StaticHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """A TCP server with a strict upper bound on concurrent request threads."""

    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def __init__(
        self,
        server_address: tuple[str, int],
        document_root: Path,
        thread_limit: int = 256,
    ) -> None:
        self.document_root = document_root.resolve(strict=True)
        if not self.document_root.is_dir():
            raise NotADirectoryError(self.document_root)
        self._worker_slots = threading.BoundedSemaphore(thread_limit)
        super().__init__(server_address, StaticHTTPRequestHandler)

    def process_request(self, request: socket.socket, client_address: object) -> None:
        self._worker_slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class StaticHTTPRequestHandler(socketserver.BaseRequestHandler):
    """Parse one request, send one response, and close the connection."""

    server: StaticHTTPServer
    request: socket.socket

    def handle(self) -> None:
        self.request.settimeout(REQUEST_TIMEOUT)
        try:
            raw_headers = self._read_headers()
            if raw_headers is None:
                return
            method, target = self._parse_request(raw_headers)
            self._serve(method, target)
        except RequestError as exc:
            self._send_error(exc.status, head=False)
        except (BrokenPipeError, ConnectionError, socket.timeout):
            return
        except OSError:
            LOGGER.exception("I/O error while serving %s", self.client_address)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, head=False)
        except Exception:
            LOGGER.exception("unexpected error while serving %s", self.client_address)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, head=False)

    def _read_headers(self) -> bytes | None:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                return None
            data.extend(chunk)
            if len(data) > MAX_HEADER_SIZE:
                raise RequestError(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)

        header_end = data.find(b"\r\n\r\n")
        return bytes(data[:header_end])

    @staticmethod
    def _parse_request(raw_headers: bytes) -> tuple[str, str]:
        lines = raw_headers.split(b"\r\n")
        if not lines or not lines[0]:
            raise RequestError(HTTPStatus.BAD_REQUEST)

        request_parts = lines[0].split()
        if len(request_parts) != 3:
            raise RequestError(HTTPStatus.BAD_REQUEST)

        try:
            method = request_parts[0].decode("ascii")
            target = request_parts[1].decode("ascii")
            version = request_parts[2].decode("ascii")
        except UnicodeDecodeError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST) from exc

        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise RequestError(HTTPStatus.HTTP_VERSION_NOT_SUPPORTED)

        for line in lines[1:]:
            if not line or line[:1] in b" \t" or b":" not in line:
                raise RequestError(HTTPStatus.BAD_REQUEST)
            name, _ = line.split(b":", maxsplit=1)
            if _HEADER_NAME.fullmatch(name) is None:
                raise RequestError(HTTPStatus.BAD_REQUEST)

        return method, target

    def _serve(self, method: str, target: str) -> None:
        is_head = method == "HEAD"
        if method not in {"GET", "HEAD"}:
            self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, head=is_head)
            return

        try:
            file_path = self._resolve_target(target)
        except RequestError as exc:
            self._send_error(exc.status, head=is_head)
            return

        try:
            file_obj = file_path.open("rb")
        except FileNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, head=is_head)
            return
        except (PermissionError, IsADirectoryError):
            self._send_error(HTTPStatus.FORBIDDEN, head=is_head)
            return

        with file_obj:
            file_stat = os.fstat(file_obj.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                self._send_error(HTTPStatus.FORBIDDEN, head=is_head)
                return

            content_type = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
            self._send_headers(
                HTTPStatus.OK,
                (
                    ("Content-Length", str(file_stat.st_size)),
                    ("Content-Type", content_type),
                ),
            )
            if not is_head:
                self._stream_file(file_obj)

    def _resolve_target(self, target: str) -> Path:
        try:
            parsed = urlsplit(target)
            if _INVALID_PERCENT_ESCAPE.search(parsed.path):
                raise RequestError(HTTPStatus.BAD_REQUEST)
            decoded_path = unquote(parsed.path, encoding="utf-8", errors="strict")
        except RequestError:
            raise
        except (UnicodeDecodeError, ValueError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST) from exc

        if parsed.scheme or parsed.netloc or not decoded_path.startswith("/"):
            raise RequestError(HTTPStatus.BAD_REQUEST)
        if "\x00" in decoded_path or "\\" in decoded_path:
            raise RequestError(HTTPStatus.BAD_REQUEST)

        path_parts = decoded_path.split("/")
        if ".." in path_parts:
            raise RequestError(HTTPStatus.FORBIDDEN)

        root = self.server.document_root
        requested_path = root.joinpath(decoded_path.lstrip("/")).resolve(strict=False)
        if not requested_path.is_relative_to(root):
            raise RequestError(HTTPStatus.FORBIDDEN)

        wants_directory = decoded_path.endswith("/")
        if wants_directory:
            if not requested_path.exists():
                raise RequestError(HTTPStatus.NOT_FOUND)
            if not requested_path.is_dir():
                raise RequestError(HTTPStatus.NOT_FOUND)
            index_path = (requested_path / "index.html").resolve(strict=False)
            if not index_path.is_relative_to(root):
                raise RequestError(HTTPStatus.FORBIDDEN)
            if not index_path.exists():
                raise RequestError(HTTPStatus.FORBIDDEN)
            return index_path

        if requested_path.is_dir():
            raise RequestError(HTTPStatus.FORBIDDEN)
        if not requested_path.exists():
            raise RequestError(HTTPStatus.NOT_FOUND)
        return requested_path

    def _send_error(self, status: HTTPStatus, *, head: bool) -> None:
        body = f"{status.value} {status.phrase}\n".encode("utf-8")
        extra_headers: list[tuple[str, str]] = [
            ("Content-Length", str(len(body))),
            ("Content-Type", "text/plain; charset=utf-8"),
        ]
        if status is HTTPStatus.METHOD_NOT_ALLOWED:
            extra_headers.append(("Allow", "GET, HEAD"))
        try:
            self._send_headers(status, tuple(extra_headers))
            if not head:
                self.request.sendall(body)
        except (BrokenPipeError, ConnectionError, OSError):
            return

    def _send_headers(
        self,
        status: HTTPStatus,
        extra_headers: tuple[tuple[str, str], ...],
    ) -> None:
        headers = [
            f"HTTP/1.1 {status.value} {status.phrase}",
            f"Server: {SERVER_NAME}",
            f"Date: {email.utils.formatdate(usegmt=True)}",
            "Connection: close",
        ]
        headers.extend(f"{name}: {value}" for name, value in extra_headers)
        response_head = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
        self.request.sendall(response_head)

    def _stream_file(self, file_obj: BinaryIO) -> None:
        while chunk := file_obj.read(FILE_CHUNK_SIZE):
            self.request.sendall(chunk)


def create_server(config: ServerConfig) -> StaticHTTPServer:
    """Construct a configured server without starting its event loop."""

    return StaticHTTPServer(
        (config.host, config.port),
        config.document_root,
        config.thread_limit,
    )
