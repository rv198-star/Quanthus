"""One immutable job-local view of captured, date-bound minute references.

This is input closure, not provider truth, a publication product or a complete
cross-job recovery/cache identity. Callers still own READY/PIT release selection.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        if value is None or isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"minute reference {field} is not a finite number") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"minute reference {field} is not finite/non-negative")
    return result


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _code(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 6 or not value.isascii() or not value.isdigit():
        raise ValueError("minute reference stock_code must be six ASCII digits")
    return value


def _view_digest(market_date: str, rows: Mapping[str, Mapping[str, Any]]) -> str:
    view = {
        "market_date": market_date,
        "rows": [
            {
                "stock_code": code,
                "volume": _text(row["volume"]),
                "close": _text(row["close"]),
                "tradestatus": row["tradestatus"],
                "first_5m_volume": _text(row["first_5m_volume"]),
            }
            for code, row in rows.items()
        ],
    }
    return hashlib.sha256(
        json.dumps(view, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ReferenceInput:
    role: str
    path: str
    sha256: str
    byte_count: int
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.byte_count,
            "rows": self.row_count,
        }


@dataclass(frozen=True)
class MinuteReferences(Mapping[str, Mapping[str, Any]]):
    market_date: str
    rows: Mapping[str, Mapping[str, Any]]
    inputs: tuple[ReferenceInput, ...]
    effective_view_sha256: str
    load_seconds: float

    def __getitem__(self, symbol: str) -> Mapping[str, Any]:
        return self.rows[symbol]

    def __iter__(self) -> Iterator[str]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def select(
        self, symbols: Sequence[str], *, require_first_five: bool = False
    ) -> MinuteReferences:
        selected = tuple(sorted(symbols))
        if not selected or len(set(selected)) != len(selected) or not set(selected).issubset(self):
            raise ValueError("minute reference selected universe is empty, duplicate or incomplete")
        if require_first_five and any(
            self[code]["tradestatus"] == "1" and self[code]["first_5m_volume"] is None
            for code in selected
        ):
            raise ValueError("native 5m opening bucket missing for selected traded symbols")
        rows = MappingProxyType({code: self[code] for code in selected})
        return MinuteReferences(
            self.market_date,
            rows,
            self.inputs,
            _view_digest(self.market_date, rows),
            self.load_seconds,
        )

    def evidence(self) -> dict[str, Any]:
        return {
            "lifecycle": "immutable-job-local-input-not-reuse-authorization",
            "market_date": self.market_date,
            "selected_symbols": list(self.rows),
            "effective_view_sha256": self.effective_view_sha256,
            "inputs": [item.to_dict() for item in self.inputs],
            "load_seconds": self.load_seconds,
            "disposition": "captured_once_in_job",
        }


def _capture(
    path: Path, columns: list[str], role: str
) -> tuple[list[dict[str, Any]], ReferenceInput]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Capture and decode the same bytes once. A mutable path cannot silently
    # supply one hash followed by different rows; no per-security re-read occurs.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("minute reference must be a regular non-symlink file")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("minute reference must be a regular non-symlink file")
        raw = source.read()
    rows = pq.read_table(pa.BufferReader(raw), columns=columns).to_pylist()
    return rows, ReferenceInput(
        role, str(path), hashlib.sha256(raw).hexdigest(), len(raw), len(rows)
    )


def parse_reference_sha256(text: str) -> dict[str, str]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("minute reference SHA JSON must be an object")
    return value


def load_minute_references(
    *,
    daily_path: Path,
    status_path: Path,
    market_date: str,
    symbols: Sequence[str] | None = None,
    five_path: Path | None = None,
    require_first_five: bool = False,
    expected_sha256: Mapping[str, str] | None = None,
) -> MinuteReferences:
    """Validate and freeze the selected view before network, key or output work."""
    started = time.monotonic()
    if date.fromisoformat(market_date).isoformat() != market_date:
        raise ValueError("minute reference market_date must be canonical ISO date")
    if require_first_five and five_path is None:
        raise ValueError("paid replay requires an explicit sealed native-5m reference")
    roles = {"sealed_daily", "sealed_status"}
    if five_path is not None:
        roles.add("sealed_native_5m")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, Mapping) or set(expected_sha256) != roles
    ):
        raise ValueError("minute reference expected SHA roles are incomplete or unknown")
    daily_rows, daily_input = _capture(
        daily_path, ["stock_code", "market_date", "volume", "close"], "sealed_daily"
    )
    status_rows, status_input = _capture(
        status_path, ["stock_code", "market_date", "tradestatus"], "sealed_status"
    )
    daily = {_code(row["stock_code"]): row for row in daily_rows}
    status = {_code(row["stock_code"]): row for row in status_rows}
    if (
        not daily
        or len(daily) != len(daily_rows)
        or len(status) != len(status_rows)
        or set(daily) != set(status)
        or any(row["market_date"] != market_date for row in daily_rows + status_rows)
        or any(str(row["tradestatus"]) not in {"0", "1"} for row in status_rows)
    ):
        raise ValueError("minute sealed daily/status evidence is incomplete or wrong-date")
    selected = tuple(sorted(daily if symbols is None else symbols))
    if not selected or len(set(selected)) != len(selected) or not set(selected).issubset(daily):
        raise ValueError("minute reference selected universe is empty, duplicate or incomplete")
    # Validate the entire sealed daily/status denominator, not only lucky canaries.
    values = {
        code: {
            "volume": _decimal(row["volume"], field="daily volume"),
            "close": _decimal(row["close"], field="daily close"),
            "tradestatus": str(status[code]["tradestatus"]),
        }
        for code, row in daily.items()
    }
    first: dict[str, Decimal] = {}
    inputs = [daily_input, status_input]
    if five_path is not None:
        five_rows, five_input = _capture(
            five_path, ["stock_code", "ts", "volume"], "sealed_native_5m"
        )
        inputs.append(five_input)
        for row in five_rows:
            code = _code(row["stock_code"])
            try:
                stamp = datetime.fromisoformat(str(row["ts"]))
            except ValueError as exc:
                raise ValueError("minute native-5m reference timestamp is invalid") from exc
            if (
                code not in daily
                or stamp.date().isoformat() != market_date
                or stamp.second != 0
                or stamp.microsecond != 0
                or (stamp.tzinfo is not None and stamp.utcoffset() != timedelta(hours=8))
            ):
                raise ValueError("minute native-5m reference is wrong-date/universe/timezone")
            if (stamp.hour, stamp.minute) == (9, 35):
                if code in first:
                    raise ValueError("minute native-5m opening bucket is duplicated")
                first[code] = _decimal(row["volume"], field="native-5m opening volume")
    if require_first_five and any(
        values[code]["tradestatus"] == "1" and code not in first for code in selected
    ):
        raise ValueError("native 5m opening bucket missing for selected traded symbols")
    if expected_sha256 is not None:
        for item in inputs:
            expected = expected_sha256[item.role]
            if not isinstance(expected, str) or expected.removeprefix("sha256:") != item.sha256:
                raise ValueError(f"minute reference captured SHA mismatch: {item.role}")
    rows = {
        code: MappingProxyType({**values[code], "first_5m_volume": first.get(code)})
        for code in selected
    }
    return MinuteReferences(
        market_date,
        MappingProxyType(rows),
        tuple(inputs),
        _view_digest(market_date, rows),
        round(time.monotonic() - started, 6),
    )
