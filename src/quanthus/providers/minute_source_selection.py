"""Fail-closed, whole-security selection of independently sourced 1m bars.

This module does not fetch, repair, or publish market data. Adapters must retain
their raw response bytes and make any opening-auction normalization explicit
before submitting a candidate. A sealed daily total cannot locate a missing
trade in an individual minute, so residual volume is never redistributed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from quanthus.providers.tencent_m1_normalize import expected_times

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class MinuteCandidate:
    provider: str
    raw_sha256: str
    raw_ref: str
    rows: tuple[Mapping[str, Any], ...]
    auction_provenance: str
    amount_status: str


@dataclass(frozen=True)
class MinuteDecision:
    status: str
    selected_provider: str | None
    selected_raw_sha256: str | None
    normalized_sha256: str | None
    rejected: tuple[tuple[str, str], ...]
    qualified: tuple[str, ...]
    amount_status: str | None


def _decimal(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite {label}")
    return number


def qualify_candidate(
    candidate: MinuteCandidate,
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    traded: bool,
) -> str:
    """Return a semantic OHLCV hash only after exact daily and grid checks.

    Amount is deliberately excluded from the semantic hash until its own
    cross-provider reconciliation is governed; its provenance is still kept in
    the decision. The candidate's raw hash identifies the original response.
    """
    if (
        not candidate.provider
        or not _SHA256.fullmatch(candidate.raw_sha256)
        or not candidate.raw_ref
    ):
        raise ValueError("missing provider, raw source SHA-256, or raw reference")
    if not candidate.auction_provenance:
        raise ValueError("opening-auction provenance is missing")
    if not candidate.amount_status:
        raise ValueError("minute amount quality status is missing")
    daily_shares = _decimal(daily_volume, "sealed daily volume")
    if not traded:
        if candidate.rows or daily_shares != 0:
            raise ValueError("suspended security has minutes or nonzero daily volume")
        return hashlib.sha256(b"suspended-zero-minutes").hexdigest()
    grid = expected_times(market_date)
    if len(candidate.rows) != len(grid):
        raise ValueError("not exactly 240 normalized minutes")
    canonical: list[tuple[str, ...]] = []
    total = Decimal(0)
    for expected, row in zip(grid, candidate.rows, strict=True):
        stamp = str(row.get("ts", ""))
        if stamp != f"{market_date} {expected[-4:-2]}:{expected[-2:]}:00":
            raise ValueError("wrong-date, missing, duplicate, or unordered minute grid")
        if row.get("stock_code") != symbol:
            raise ValueError("minute security code mismatch")
        values = {
            name: _decimal(row.get(name), name)
            for name in ("open", "high", "low", "close", "volume")
        }
        if (
            values["volume"] < 0
            or values["high"] < max(values["open"], values["close"], values["low"])
            or values["low"] > min(values["open"], values["close"], values["high"])
        ):
            raise ValueError("invalid minute OHLCV")
        amount = row.get("amount")
        if amount is not None and _decimal(amount, "amount") < 0:
            raise ValueError("negative minute amount")
        total += values["volume"]
        canonical.append((stamp, *(str(values[name].normalize()) for name in values)))
    if total != daily_shares:
        raise ValueError(f"minute/daily share volume mismatch: {total - daily_shares}")
    if _decimal(candidate.rows[-1].get("close"), "last close") != _decimal(
        daily_close, "sealed daily close"
    ):
        raise ValueError("minute/daily close mismatch")
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


def choose_whole_security(
    candidates: Sequence[MinuteCandidate],
    *,
    symbol: str,
    market_date: str,
    daily_volume: Any,
    daily_close: Any,
    traded: bool,
    priority: Sequence[str],
) -> MinuteDecision:
    """Choose a single qualified source; disagreeing qualified sources block.

    All supplied candidates are independently checked. `priority` is a
    versioned policy input, never derived from response order or latency.
    """
    if len(priority) != len(set(priority)) or not priority:
        raise ValueError("source priority must be a nonempty unique sequence")
    if len(candidates) != len({item.provider for item in candidates}):
        raise ValueError("duplicate candidate provider")
    if any(item.provider not in priority for item in candidates):
        raise ValueError("candidate provider is absent from source policy")
    rejected: list[tuple[str, str]] = []
    passing: list[tuple[MinuteCandidate, str]] = []
    for candidate in candidates:
        try:
            semantic_hash = qualify_candidate(
                candidate,
                symbol=symbol,
                market_date=market_date,
                daily_volume=daily_volume,
                daily_close=daily_close,
                traded=traded,
            )
        except ValueError as exc:
            rejected.append((candidate.provider, str(exc)))
        else:
            passing.append((candidate, semantic_hash))
    if not passing:
        return MinuteDecision(
            "pending_no_qualified_source", None, None, None, tuple(rejected), (), None
        )
    qualified = tuple(
        provider for provider in priority if any(c.provider == provider for c, _ in passing)
    )
    if len({digest for _, digest in passing}) != 1:
        return MinuteDecision(
            "blocked_cross_source_conflict", None, None, None, tuple(rejected), qualified, None
        )
    chosen, semantic_hash = next(
        (c, digest) for provider in priority for c, digest in passing if c.provider == provider
    )
    return MinuteDecision(
        "qualified_whole_security_candidate",
        chosen.provider,
        chosen.raw_sha256,
        semantic_hash,
        tuple(rejected),
        qualified,
        chosen.amount_status,
    )
