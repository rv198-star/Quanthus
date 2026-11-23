"""In-memory P11 feature adapter: canonical rows -> original panel SQL -> facts.

This adapter consumes the already-read, already-qualified in-memory daily inputs P12
delivers (bars, corporate actions, exceptions, price-limit facts, calendar, identity)
and produces the two things the rule seam and the expert context consume:

* `CandidateDailyInput` rows for the event scan, and
* `SecurityFeatureFacts` carrying the real computed feature values plus the raw
  P10-supplied identity/price fields, bound to one signal date and knowledge cutoff.

It does not re-implement the feature formulas.  The canonical rows are registered as
real job-local `source_*` temp relations in a `:memory:` DuckDB connection and the
ORIGINAL `_feature_panel_query` (baseline_research.py) is executed over them, so the
returned BaselineFeatureRow is bit-identical to the production panel on the same input.
The same `_validate_feature_sources` audit the production path runs is applied first,
so duplicate bars, unmapped identities, invalid actions and invalid exception intervals
fail closed exactly as they do in the materialized pipeline.

A missing or non-equivalent field is blocked, never defaulted: a corporate action only
applies when the source validation keeps it (known_at < effective_date), a limit member
left unbound keeps entry_eligible False, and a short history yields feature_ready=False
rather than a shrunk window.

This is job-local TMP: no physical store is read, no warehouse is created, and nothing
is cached across jobs.  The full semantic input closure (the exact rows + the code that
generated the query) is the identity of the result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.backtest.baseline_research import (
    BaselineFeatureRow,
    BaselineResearchError,
    _feature_panel_query,
    _validate_feature_sources,
)
from quanthus.event_library.scan import CandidateDailyInput
from quanthus.product_daily.contracts import (
    ProductDailyError,
    require_aware,
    require_text,
)
from quanthus.product_daily.recommendation.rule_seam import SecurityFeatureFacts


@dataclass(frozen=True)
class DailyBar:
    """One qualified daily bar for one security, already PIT-trimmed to the cutoff."""

    security_id: str
    session_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    amount: Decimal  # CNY turnover (amount), not share volume; never interchanged
    is_tradable: bool = True


@dataclass(frozen=True)
class CorporateAction:
    """A normalized corporate action effective on one date; knowable before it applies.

    security_code is the exchange code the raw source keys on; the identity map resolves
    it to the security_id the panel consumes.  known_at must precede effective_date,
    matching the panel's mapped_actions filter; the source audit rejects a row that
    violates it (fail closed, not silently kept).
    """

    security_code: str
    effective_date: date
    cash_dividend_per_share: Decimal
    bonus_share_ratio: Decimal
    known_at: date


@dataclass(frozen=True)
class ExceptionSpan:
    """A corporate-action exception window blocking the affected session(s).

    security_code is the exchange code the raw source keys on, resolved through the
    same identity map the production pipeline uses.
    """

    security_code: str
    blocked_from: date
    blocked_through: date | None


@dataclass(frozen=True)
class LimitFacts:
    """Per-session price-limit / execution-policy facts for one security.

    None members mean the P10-qualified limit member did not supply the field for that
    session; the consumers that depend on it (entry_eligible, unexplained-transition
    classification) then take the panel's NULL path, never a defaulted value.
    """

    session_date: date
    entry_policy_allows: bool | None
    is_executable: bool | None
    upper_limit_pct: Decimal | None
    lower_limit_pct: Decimal | None
    upper_limit_price: Decimal | None
    lower_limit_price: Decimal | None


@dataclass(frozen=True)
class IdentityFacts:
    """The P10-supplied raw identity / dependency facts for one security."""

    security_id: str
    stock_code: str  # the code the source identity table keys on (may differ from listing)
    listing_code: str
    security_name: str
    industry_id: str
    venue_id: str
    instrument_type: str
    listing_id: str
    company_id: str
    depends_on_member_ids: tuple[str, ...]
    invalidations: tuple[str, ...]
    price_bound: Decimal | None
    source_evidence_digest: str


@dataclass(frozen=True)
class CalendarView:
    """The authoritative trading calendar / universe membership the listing floor uses.

    trading_dates is every session the security is a universe member for the job (the
    source_daily_universe projection); the panel's listing_sessions is the row_number
    over those memberships, not a warmup-row count.  listing_sessions is the pre-computed
    value for the signal date supplied by the calendar/listing fact.
    """

    trading_dates: tuple[date, ...]
    listing_sessions: int


def _opt(value: Decimal | None) -> str:
    return "NULL" if value is None else f"CAST('{value}' AS DOUBLE)"


def _opt_bool(value: bool | None) -> str:
    return "NULL" if value is None else ("TRUE" if value else "FALSE")


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _register_source_relations(
    connection: Any,
    *,
    security_id: str,
    stock_code: str,
    bars: Sequence[DailyBar],
    actions: Sequence[CorporateAction],
    exceptions: Sequence[ExceptionSpan],
    limits: Sequence[LimitFacts],
    trading_dates: Sequence[date],
) -> None:
    """Register the canonical in-memory rows as the source_* relations the SQL reads."""

    connection.execute(
        "CREATE TABLE source_security_code_validity(security_id VARCHAR, stock_code VARCHAR)"
    )
    # One row per (security_id, stock_code); identity comes from the caller's
    # IdentityFacts, so a code ambiguity is rejected by _validate_feature_sources.
    connection.execute(
        "INSERT INTO source_security_code_validity VALUES (?, ?)",
        (security_id, stock_code),
    )

    connection.execute("CREATE TABLE source_daily_universe(security_id VARCHAR, market_date DATE)")
    if trading_dates:
        connection.executemany(
            "INSERT INTO source_daily_universe VALUES (?, ?)",
            [(security_id, d) for d in trading_dates],
        )

    connection.execute(
        "CREATE TABLE source_daily_ohlcv_none(security_id VARCHAR, date DATE, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE)"
    )
    if bars:
        connection.executemany(
            "INSERT INTO source_daily_ohlcv_none VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    b.security_id,
                    b.session_date,
                    float(b.open_price),
                    float(b.high_price),
                    float(b.low_price),
                    float(b.close_price),
                    float(b.volume),
                    float(b.amount),
                )
                for b in bars
            ],
        )

    connection.execute(
        "CREATE TABLE source_price_limit_execution_daily(security_id VARCHAR, "
        "execution_date DATE, entry_policy_allows BOOLEAN, is_executable BOOLEAN, "
        "upper_limit_pct DOUBLE, lower_limit_pct DOUBLE, "
        "upper_limit_price DOUBLE, lower_limit_price DOUBLE)"
    )
    if limits:
        connection.executemany(
            "INSERT INTO source_price_limit_execution_daily VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    security_id,
                    lm.session_date,
                    lm.entry_policy_allows,
                    lm.is_executable,
                    None if lm.upper_limit_pct is None else float(lm.upper_limit_pct),
                    None if lm.lower_limit_pct is None else float(lm.lower_limit_pct),
                    None if lm.upper_limit_price is None else float(lm.upper_limit_price),
                    None if lm.lower_limit_price is None else float(lm.lower_limit_price),
                )
                for lm in limits
            ],
        )

    connection.execute(
        "CREATE TABLE source_corporate_actions_normalized(security_code VARCHAR, "
        "effective_date DATE, known_at DATE, cash_dividend_per_share DOUBLE, "
        "bonus_share_ratio DOUBLE)"
    )
    if actions:
        connection.executemany(
            "INSERT INTO source_corporate_actions_normalized VALUES (?,?,?,?,?)",
            [
                (
                    a.security_code,
                    a.effective_date,
                    a.known_at,
                    float(a.cash_dividend_per_share),
                    float(a.bonus_share_ratio),
                )
                for a in actions
            ],
        )

    connection.execute(
        "CREATE TABLE source_corporate_action_exceptions(security_code VARCHAR, "
        "blocked_from DATE, blocked_through DATE)"
    )
    if exceptions:
        connection.executemany(
            "INSERT INTO source_corporate_action_exceptions VALUES (?,?,?)",
            [(e.security_code, e.blocked_from, e.blocked_through) for e in exceptions],
        )

    # The raw source tables key on security_code (the exchange code); the production
    # path resolves it to security_id through feature_identity_map built from
    # source_security_code_validity.  Mirror that exactly so the same validator and
    # the same query see the identical shape the physical pipeline produces.
    connection.execute(
        "CREATE VIEW feature_identity_map AS "
        "SELECT stock_code, min(security_id) AS security_id "
        "FROM source_security_code_validity "
        "GROUP BY stock_code "
        "HAVING count(DISTINCT security_id) = 1"
    )
    for source in (
        "source_corporate_actions_normalized",
        "source_corporate_action_exceptions",
    ):
        connection.execute(
            f"CREATE VIEW {source}_mapped AS "
            f"SELECT source.*, identity_map.security_id "
            f"FROM {source} source "
            f"JOIN feature_identity_map identity_map "
            f"  ON identity_map.stock_code = source.security_code"
        )


def compute_security_feature_facts(
    *,
    security_id: str,
    bars: Sequence[DailyBar],
    actions: Sequence[CorporateAction],
    exceptions: Sequence[ExceptionSpan],
    limits: Sequence[LimitFacts],
    trading_dates: Sequence[date],
    identity: IdentityFacts,
    signal_date: date,
    signal_close: datetime,
) -> SecurityFeatureFacts:
    """Compute one security's real feature row on the signal date via the original SQL.

    The bars must be the qualified, already-PIT-trimmed history; trading_dates is the
    universe membership the listing-session count runs over (so the first delivered
    date is the scope start, matching the production scope).  The original
    _feature_panel_query is executed against job-local source_* relations, so the
    returned row is bit-identical to the production panel on the same input.
    """

    try:
        import duckdb
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise ProductDailyError(
            "feature_adapter_dependency", "duckdb is required for the feature panel"
        ) from exc

    require_text(security_id, "security_id")
    require_aware(signal_close, "signal_close")
    if not bars:
        raise ProductDailyError("missing_bars", "no qualified bars for this security")
    ordered_dates = sorted(set(trading_dates))
    if not ordered_dates or ordered_dates[-1] != signal_date:
        raise ProductDailyError(
            "missing_signal_membership",
            "the universe membership must end on the declared signal_date",
        )
    bar_dates = {b.session_date for b in bars}
    if signal_date not in bar_dates:
        raise ProductDailyError(
            "missing_signal_bar", "the bar history must end on the declared signal_date"
        )
    # The closure must be single-security: a foreign bar/limit would contaminate the
    # universe membership and silently change this security's features.
    foreign = [b.security_id for b in bars if b.security_id != security_id]
    if foreign:
        raise ProductDailyError(
            "cross_security_contamination",
            "the bar closure contains a row for a different security",
        )

    connection = duckdb.connect(":memory:")
    try:
        _register_source_relations(
            connection,
            security_id=security_id,
            stock_code=identity.stock_code,
            bars=bars,
            actions=actions,
            exceptions=exceptions,
            limits=limits,
            trading_dates=ordered_dates,
        )
        try:
            _validate_feature_sources(connection)
        except BaselineResearchError as exc:
            raise ProductDailyError("invalid_feature_sources", str(exc)) from exc

        query = _feature_panel_query(ordered_dates[0], signal_date, output_order="date-security")
        cursor = connection.execute(query)
        names = [str(item[0]) for item in cursor.description]
        rows = [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]
    finally:
        connection.close()

    signal_rows = [r for r in rows if r["market_date"] == signal_date]
    if len(signal_rows) != 1:
        raise ProductDailyError(
            "feature_row_missing", "the panel produced no row for the signal date"
        )
    feature_row = BaselineFeatureRow.from_mapping(signal_rows[0])
    full = signal_rows[0]  # for the excluded columns BaselineFeatureRow drops

    return SecurityFeatureFacts(
        security_code=identity.listing_code,
        signal_date=signal_date,
        rsi14=feature_row.rsi14,
        realized_volatility20=feature_row.realized_volatility20,
        listing_sessions=feature_row.listing_sessions,
        total_return_index=feature_row.total_return_index,
        sma20=feature_row.sma20,
        sma60=feature_row.sma60,
        momentum20=feature_row.momentum20,
        security_name=identity.security_name,
        industry_id=identity.industry_id,
        mark_price=feature_row.close_price,
        price_bound=identity.price_bound,
        invalidations=identity.invalidations,
        depends_on_member_ids=identity.depends_on_member_ids,
        volume_ratio20=feature_row.volume_ratio20,
        average_amount20=feature_row.average_amount20,
        daily_total_return=feature_row.daily_total_return,
        prior_high20=feature_row.prior_high20,
        feature_ready=feature_row.feature_ready,
        entry_eligible=feature_row.entry_eligible,
        exception_active=bool(full["exception_active"]),
        unexplained_price_transition=bool(full["unexplained_price_transition"]),
        scope_continuity_excluded=bool(full["scope_continuity_excluded"]),
    )


def daily_input_from_bar(
    *,
    bar: DailyBar,
    identity: IdentityFacts,
    listing_sessions: int,
    close_at: datetime,
    knowledge_cutoff: datetime,
    source_evidence_digest: str,
    adjustment_effective: bool | None = None,
) -> CandidateDailyInput:
    """Project one qualified bar + identity into a CandidateDailyInput for the scan.

    close_at is the session close instant the calendar supplies for this date (15:00
    exchange time under the daily profile); it is not invented here.  turnover is the
    CNY amount (amount), not share volume.
    """

    require_aware(close_at, "close_at")
    require_aware(knowledge_cutoff, "knowledge_cutoff")
    return CandidateDailyInput(
        security_id=identity.security_id,
        listing_id=identity.listing_id,
        listing_code=identity.listing_code,
        company_id=identity.company_id,
        venue_id=identity.venue_id,
        instrument_type=identity.instrument_type,
        static_level1_industry_id=identity.industry_id,
        session_date=bar.session_date,
        close_at=close_at,
        knowledge_cutoff=knowledge_cutoff,
        open_price=bar.open_price,
        high_price=bar.high_price,
        low_price=bar.low_price,
        close_price=bar.close_price,
        turnover=bar.amount,  # CNY amount, not share volume
        is_tradable=bar.is_tradable,
        listing_sessions=listing_sessions,
        adjustment_effective=adjustment_effective,
        source_evidence_digest=source_evidence_digest,
    )
