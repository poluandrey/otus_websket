"""Command-line entry point."""

from __future__ import annotations

import logging

from .config import load_config
from .server import create_server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    with create_server(config) as server:
        host, port = server.server_address
        logging.info("serving %s on http://%s:%s", config.document_root, host, port)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            logging.info("server stopped")


if __name__ == "__main__":
    main()
