"""Transport safeguards for BaoStock's process-global socket."""

from __future__ import annotations

import importlib
from typing import Any


class BaostockTransportError(RuntimeError):
    """Raised when a live BaoStock socket cannot be bounded."""


class BaostockAccessDenied(RuntimeError):
    """The supplier explicitly refused access; do not reconnect or scan onward."""


def require_access(error_code: Any, error_msg: Any) -> None:
    message = str(error_msg).lower()
    if str(error_code) == "10001011" or "黑名单" in message or "blacklist" in message:
        raise BaostockAccessDenied("BAOSTOCK_ACCESS_DENIED: " + str(error_code))


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
