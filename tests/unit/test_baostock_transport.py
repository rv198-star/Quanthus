from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import quanthus.providers.baostock_transport as transport


class BaostockTransportTests(unittest.TestCase):
    def test_set_socket_timeout_configures_live_baostock_socket(self) -> None:
        class Socket:
            timeout: float | None = None

            def settimeout(self, value: float) -> None:
                self.timeout = value

        sock = Socket()
        with patch.object(
            transport.importlib,
            "import_module",
            return_value=SimpleNamespace(default_socket=sock),
        ):
            self.assertTrue(transport.set_socket_timeout(30))
        self.assertEqual(sock.timeout, 30.0)

    def test_set_socket_timeout_rejects_missing_live_socket(self) -> None:
        with patch.object(
            transport.importlib,
            "import_module",
            return_value=SimpleNamespace(default_socket=None),
        ), self.assertRaisesRegex(
            transport.BaostockTransportError, "without a default socket"
        ):
            transport.set_socket_timeout(30)


if __name__ == "__main__":
    unittest.main()
