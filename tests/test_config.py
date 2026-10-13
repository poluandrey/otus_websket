from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from http_server.config import load_config, read_config_file


class ConfigTests(unittest.TestCase):
    def test_reads_assignment_config_and_cli_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "httpd.conf"
            config_path.write_text(
                "cpu_limit 4 # ignored by the threaded server\n"
                "thread_limit 12\n"
                f"document_root {root}\n",
                encoding="utf-8",
            )

            config = load_config(["--config", str(config_path), "--port", "8080"])

            self.assertEqual(config.port, 8080)
            self.assertEqual(config.thread_limit, 12)
            self.assertEqual(config.document_root, root.resolve())

    def test_rejects_malformed_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "httpd.conf"
            config_path.write_text("thread_limit\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "invalid config line"):
                read_config_file(config_path)


if __name__ == "__main__":
    unittest.main()
