"""Transport safeguards for BaoStock's process-global socket."""

from __future__ import annotations

import importlib
from typing import Any


class BaostockTransportError(RuntimeError):
    """Raised when a live BaoStock socket cannot be bounded."""


def set_socket_timeout(timeout_seconds: float) -> bool:
    """Set a timeout on BaoStock's process-global socket after login.

    BaoStock 0.9.x performs blocking ``recv`` calls without a timeout. The import
    can be unavailable in unit tests that inject a lightweight fake module; in
    that case there is no live socket to configure and ``False`` is returned.
    """

    if timeout_seconds <= 0:
        raise ValueError("BaoStock socket timeout must be positive")
    try:
        context: Any = importlib.import_module("baostock.common.context")
    except ModuleNotFoundError:
        return False
    sock = getattr(context, "default_socket", None)
    if sock is None:
        raise BaostockTransportError("BaoStock login succeeded without a default socket")
    try:
        sock.settimeout(float(timeout_seconds))
    except Exception as exc:  # noqa: BLE001
        raise BaostockTransportError(f"cannot set BaoStock socket timeout: {exc}") from exc
    return True
