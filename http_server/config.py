"""Configuration loading for the HTTP server."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_CONFIG_PATH = Path("/etc/httpd.conf")


@dataclass(frozen=True)
class ServerConfig:
    """Runtime settings for the server."""

    host: str
    port: int
    document_root: Path
    thread_limit: int


def read_config_file(path: Path) -> dict[str, str]:
    """Read the simple ``key value`` format used by the homework tests."""

    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as config_file:
        for line_number, raw_line in enumerate(config_file, start=1):
            line = raw_line.partition("#")[0].strip()
            if not line:
                continue

            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError(f"invalid config line {line_number}: {raw_line.rstrip()!r}")
            key, value = parts
            values[key] = value.strip()

    return values


def _positive_int(value: str, setting: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{setting} must be an integer") from exc
    if number <= 0:
        raise ValueError(f"{setting} must be positive")
    return number


def load_config(argv: Sequence[str] | None = None) -> ServerConfig:
    """Combine command-line arguments with an optional ``httpd.conf`` file."""

    parser = argparse.ArgumentParser(description="Serve static files over HTTP")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--document-root", type=Path)
    parser.add_argument("--thread-limit", type=int)
    args = parser.parse_args(argv)

    file_values: dict[str, str] = {}
    if args.config.is_file():
        file_values = read_config_file(args.config)

    # cpu_limit belongs to the original assignment's non-blocking-server
    # configuration. Validate it when present, although this implementation
    # deliberately uses a bounded pool of request threads.
    if "cpu_limit" in file_values:
        _positive_int(file_values["cpu_limit"], "cpu_limit")

    port = args.port if args.port is not None else int(file_values.get("port", "80"))
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")

    raw_thread_limit = (
        str(args.thread_limit)
        if args.thread_limit is not None
        else file_values.get("thread_limit", "256")
    )
    thread_limit = _positive_int(raw_thread_limit, "thread_limit")

    document_root = args.document_root
    if document_root is None:
        document_root = Path(file_values.get("document_root", Path.cwd() / "www"))

    host = args.host if args.host is not None else file_values.get("host", "0.0.0.0")
    return ServerConfig(
        host=host,
        port=port,
        document_root=document_root.expanduser().resolve(),
        thread_limit=thread_limit,
    )
