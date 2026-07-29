"""Versioned, offline benchmark curves for reproducible backtest comparisons."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

BENCHMARK_CURVE_SCHEMA_VERSION = 1
RATE_QUANTUM = Decimal("0.00000001")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class BenchmarkCurveError(ValueError):
    """Raised when a benchmark artifact violates its reproducibility contract."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkCurveError(f"{path} must be a non-empty string")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise BenchmarkCurveError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _parse_close(value: object, path: str) -> Decimal:
    if not isinstance(value, str):
        raise BenchmarkCurveError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BenchmarkCurveError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BenchmarkCurveError(f"{path} must be finite and positive")
    return parsed


@dataclass(frozen=True)
class BenchmarkObservation:
    market_date: date
    close: Decimal

    def __post_init__(self) -> None:
        if not self.close.is_finite() or self.close <= 0:
            raise BenchmarkCurveError("benchmark close must be finite and positive")

    def to_dict(self) -> dict[str, str]:
        return {"date": self.market_date.isoformat(), "close": str(self.close)}


@dataclass(frozen=True)
class BenchmarkComparison:
    benchmark_id: str
    instrument_id: str
    curve_digest: str
    start_date: date
    end_date: date
    start_close: Decimal
    end_close: Decimal
    observation_count: int
    total_return: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "instrument_id": self.instrument_id,
            "curve_digest": self.curve_digest,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "start_close": str(self.start_close),
            "end_close": str(self.end_close),
            "observation_count": self.observation_count,
            "total_return": str(self.total_return),
        }


@dataclass(frozen=True)
class BenchmarkCurve:
    benchmark_id: str
    instrument_id: str
    display_name: str
    currency: str
    price_field: str
    source: str
    source_revision: str
    acquired_at: datetime
    observations: tuple[BenchmarkObservation, ...]
    schema_version: int = BENCHMARK_CURVE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, path in (
            (self.benchmark_id, "benchmark_id"),
            (self.instrument_id, "instrument_id"),
            (self.display_name, "display_name"),
            (self.currency, "currency"),
            (self.price_field, "price_field"),
            (self.source, "source"),
            (self.source_revision, "source_revision"),
        ):
            _require_text(value, path)
        if self.schema_version != BENCHMARK_CURVE_SCHEMA_VERSION:
            raise BenchmarkCurveError("unsupported benchmark curve schema version")
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise BenchmarkCurveError("acquired_at must be timezone-aware")
        if self.price_field != "close":
            raise BenchmarkCurveError("benchmark price_field must be close")
        if not self.observations:
            raise BenchmarkCurveError("benchmark curve must contain observations")
        dates = tuple(item.market_date for item in self.observations)
        if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
            raise BenchmarkCurveError("benchmark observations must be unique and date-ascending")

    def payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "instrument_id": self.instrument_id,
            "display_name": self.display_name,
            "currency": self.currency,
            "price_field": self.price_field,
            "source": self.source,
            "source_revision": self.source_revision,
            "acquired_at": self.acquired_at.isoformat(),
            "observations": [item.to_dict() for item in self.observations],
        }

    @property
    def digest(self) -> str:
        return _digest(self.payload_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_dict(), "digest": self.digest}

    def compare(self, required_dates: tuple[date, ...]) -> BenchmarkComparison:
        if not required_dates:
            raise BenchmarkCurveError("benchmark comparison requires replay dates")
        if required_dates != tuple(sorted(required_dates)) or len(required_dates) != len(
            set(required_dates)
        ):
            raise BenchmarkCurveError("replay dates must be unique and date-ascending")
        by_date = {item.market_date: item.close for item in self.observations}
        missing = tuple(value for value in required_dates if value not in by_date)
        if missing:
            preview = ", ".join(value.isoformat() for value in missing[:5])
            raise BenchmarkCurveError(
                f"benchmark curve is missing {len(missing)} replay dates; first={preview}"
            )
        start_close = by_date[required_dates[0]]
        end_close = by_date[required_dates[-1]]
        total_return = (end_close / start_close - Decimal("1")).quantize(
            RATE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        return BenchmarkComparison(
            benchmark_id=self.benchmark_id,
            instrument_id=self.instrument_id,
            curve_digest=self.digest,
            start_date=required_dates[0],
            end_date=required_dates[-1],
            start_close=start_close,
            end_close=end_close,
            observation_count=len(required_dates),
            total_return=total_return,
        )


def load_benchmark_curve(path: Path) -> BenchmarkCurve:
    """Load and checksum-verify one immutable local benchmark curve artifact."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchmarkCurveError(f"cannot read benchmark curve: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkCurveError(f"invalid benchmark curve JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise BenchmarkCurveError("benchmark curve must be a JSON object")
    _require_exact_keys(
        raw,
        {
            "schema_version",
            "benchmark_id",
            "instrument_id",
            "display_name",
            "currency",
            "price_field",
            "source",
            "source_revision",
            "acquired_at",
            "observations",
            "digest",
        },
        "benchmark_curve",
    )
    if not isinstance(raw["schema_version"], int) or isinstance(raw["schema_version"], bool):
        raise BenchmarkCurveError("schema_version must be an integer")
    try:
        acquired_at = datetime.fromisoformat(_require_text(raw["acquired_at"], "acquired_at"))
    except ValueError as exc:
        raise BenchmarkCurveError("acquired_at must be an ISO datetime") from exc
    observations_raw = raw["observations"]
    if not isinstance(observations_raw, list):
        raise BenchmarkCurveError("observations must be an array")
    observations: list[BenchmarkObservation] = []
    for index, item in enumerate(observations_raw):
        if not isinstance(item, dict):
            raise BenchmarkCurveError(f"observations[{index}] must be an object")
        _require_exact_keys(item, {"date", "close"}, f"observations[{index}]")
        try:
            market_date = date.fromisoformat(
                _require_text(item["date"], f"observations[{index}].date")
            )
        except ValueError as exc:
            raise BenchmarkCurveError(f"observations[{index}].date must be an ISO date") from exc
        observations.append(
            BenchmarkObservation(
                market_date=market_date,
                close=_parse_close(item["close"], f"observations[{index}].close"),
            )
        )
    curve = BenchmarkCurve(
        schema_version=raw["schema_version"],
        benchmark_id=_require_text(raw["benchmark_id"], "benchmark_id"),
        instrument_id=_require_text(raw["instrument_id"], "instrument_id"),
        display_name=_require_text(raw["display_name"], "display_name"),
        currency=_require_text(raw["currency"], "currency"),
        price_field=_require_text(raw["price_field"], "price_field"),
        source=_require_text(raw["source"], "source"),
        source_revision=_require_text(raw["source_revision"], "source_revision"),
        acquired_at=acquired_at,
        observations=tuple(observations),
    )
    declared_digest = _require_text(raw["digest"], "digest")
    if not _SHA256.fullmatch(declared_digest):
        raise BenchmarkCurveError("digest must use sha256:<64 lowercase hex characters>")
    if declared_digest != curve.digest:
        raise BenchmarkCurveError("benchmark curve digest mismatch")
    return curve
