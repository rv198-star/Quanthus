"""Actual bounded P10 observations, never a complete image or consumer admission.

Adapters consume one immutable history dependency. Retained source clocks and
values are preserved; missing historical acquisition cannot become a PIT cutoff.
Unresolved corrections remain separate from uncontroversial new observations.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _check_row, business_date, digest, file_sha, partition
from .base22_august import dividend_date
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_sources import SourceAssessmentError
from .disclosure_event import (
    _stable_id,
    materialize_adjustment_factor_rows,
    materialize_announcement_rows,
    materialize_corporate_action_rows,
)
from .financial_reporting import (
    FinancialCompanyResolver,
    FinancialReportingSource,
    expand_financial_native_row,
)
from .ownership_capital import materialize_shareholder_count_row

SHANGHAI = ZoneInfo("Asia/Shanghai")


def validate_tail_links(scan: GlobalScan) -> None:
    """Join exact identities first; never materialize listing x calendar pairs."""
    scan.reject(
        """SELECT 1 FROM dwd_security_bar b
        LEFT JOIN dwd_listing l ON b.listing_id=l.listing_id
        LEFT JOIN dwd_trading_calendar c ON b.calendar_session_id=c.calendar_session_id
        WHERE l.listing_id IS NULL OR c.calendar_session_id IS NULL
        OR c.venue_id<>l.venue_id
        OR c.trade_date<>CAST(timezone('Asia/Shanghai',b.observation_start) AS DATE)
        OR l.effective_from>b.observation_start
        OR (l.effective_to IS NOT NULL AND b.observation_start>=l.effective_to)
        OR l.knowledge_from>b.knowledge_from
        OR (l.knowledge_to IS NOT NULL AND b.knowledge_from>=l.knowledge_to)
        OR c.knowledge_from>b.knowledge_from
        OR (c.knowledge_to IS NOT NULL AND b.knowledge_from>=c.knowledge_to) LIMIT 1""",
        "TAIL_GLOBAL_CALENDAR_LISTING_KNOWLEDGE_CLOSURE_FAILED",
    )


def instant(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise SourceAssessmentError("OBSERVATION_CLOCK_INVALID") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SourceAssessmentError("OBSERVATION_CLOCK_UNKNOWN")
    return value.astimezone(timezone.utc)


def acquisition(row: Mapping[str, Any], parent: datetime) -> datetime:
    """Only actual acquisition instants, never built_at or a publication date."""
    clocks = [instant(row.get("acquired_at")), instant(parent)]
    if row.get("provider_acquired_at") not in (None, ""):
        clocks.append(instant(row["provider_acquired_at"]))
    return max(clocks)


def tail_observation(
    raw: Mapping[str, Any],
    *,
    listing: Mapping[str, Any],
    calendar: Mapping[str, Any],
    observed_at: datetime,
    source_sha256: str,
    revision: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """New retained-raw knowledge version, including genuine invalid observations.

    Zero/nontrading raw observations are retained with explicit quarantine evidence;
    this adapter cannot alter tradability or make them entry-admissible.
    """
    day = raw["trade_date"]
    start = datetime.combine(day, time(), SHANGHAI).astimezone(timezone.utc)
    knowledge = instant(observed_at)
    if (
        not isinstance(day, date)
        or day > THROUGH
        or calendar["trade_date"] != day
        or calendar["venue_id"] != listing["venue_id"]
    ):
        raise SourceAssessmentError("TAIL_CALENDAR_DATE_VENUE_MISMATCH")
    if not (
        listing["effective_from"] <= start
        and (listing["effective_to"] is None or start < listing["effective_to"])
    ):
        raise SourceAssessmentError("TAIL_LISTING_DATE_MISMATCH")
    for parent in (listing, calendar):
        if parent["knowledge_from"] > knowledge or (
            parent["knowledge_to"] is not None and knowledge >= parent["knowledge_to"]
        ):
            raise SourceAssessmentError("TAIL_PARENT_KNOWLEDGE_MISMATCH")
    numeric = {
        target: raw[field]
        for field, target in {
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "turnover",
        }.items()
    }
    if any(
        not isinstance(v, Decimal) or not v.is_finite() or int(v.as_tuple().exponent) < -10
        for v in numeric.values()
    ):
        raise SourceAssessmentError("TAIL_DECIMAL_LOSS")
    invalid = (
        raw["tradestatus"] != "1"
        or not calendar["is_open"]
        or any(raw[f] == 0 for f in ("volume", "amount"))
    )
    output = {
        "record_source": "p10_retained_baostock_tail",
        "source_record_id": f"stock_code={raw['stock_code']}&trade_date={day}",
        "source_revision": revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge,
        "knowledge_to": None,
        "observation_start": start,
        "observation_end": start + timedelta(days=1),
        "listing_id": listing["listing_id"],
        "calendar_session_id": calendar["calendar_session_id"],
        "interval_size": "1d",
        "price_basis": "UNADJUSTED",
        "currency": listing["listing_currency"],
        **numeric,
        "trade_count": None,
    }
    return output, {
        "trade_date": str(day),
        "raw_row_sha256": digest(raw),
        "output_sha256": digest(output),
        "listing_row_sha256": digest(listing),
        "calendar_row_sha256": digest(calendar),
        "provider_acquisition": None,
        "warehouse_observed_at": knowledge.isoformat(),
        "historical_accepted_child": "NOT_ASSERTED",
        "state_mutations": 0,
        "classification": "QUARANTINED_NONTRADING_OR_ZERO_ACTIVITY"
        if invalid
        else "RAW_OBSERVATION",
        "raw_nontrading": raw["tradestatus"] != "1",
        "calendar_is_open": calendar["is_open"],
        "zero_volume": raw["volume"] == 0,
        "zero_turnover": raw["amount"] == 0,
        "consumer_admission": "NOT_GRANTED",
    }


def announcement_observation(
    row: dict[str, Any],
    *,
    company_id: str,
    security_id: str,
    parent_knowledge: datetime,
    source_sha256: str,
    revision: str,
) -> dict[str, list[dict[str, Any]]]:
    knowledge = acquisition(row, parent_knowledge)
    result = materialize_announcement_rows(
        row,
        company_id=company_id,
        security_id=security_id,
        source_sha256=source_sha256,
        source_revision=revision,
        parent_knowledge_from=parent_knowledge,
    )
    for rows in result.values():
        for output in rows:
            if business_date(output["effective_at"]) != dividend_date(row["announce_date"]):
                raise SourceAssessmentError("ANNOUNCEMENT_PUBLICATION_DATE_CONFLICT")
            output["knowledge_from"] = max(knowledge, output["effective_at"])
    return result


def weekly_action_observation(
    row: dict[str, Any],
    *,
    company_id: str,
    security_id: str,
    parent_knowledge: datetime,
    source_sha256: str,
    revision: str,
    vocabulary: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    knowledge = acquisition(row, parent_knowledge)
    announced = dividend_date(row["announce_date"])
    effective = dividend_date(row["ex_date"]) if row.get("ex_date") else announced
    if effective > THROUGH:
        return {}
    mapped = dict(row, built_at=knowledge, announce_date=announced, ex_date=effective)
    result = materialize_corporate_action_rows(
        mapped,
        company_id=company_id,
        security_id=security_id,
        source_id="base22_weekly/corporate_actions",
        source_sha256=source_sha256,
        source_revision=revision,
        vocabulary=vocabulary,
        parent_knowledge_from=parent_knowledge,
    )
    for rows in result.values():
        for output in rows:
            output["announced_at"] = None  # date precision, not an invented public instant
    return result


def holder_observation(
    row: dict[str, Any],
    *,
    company_id: str,
    security_id: str,
    parent_knowledge: datetime,
    source_sha256: str,
    revision: str,
) -> dict[str, list[dict[str, Any]]]:
    knowledge = acquisition(row, parent_knowledge)
    day = dividend_date(row["end_date"])
    if day > THROUGH:
        return {}
    # The old adapter needs a clock. Pass the real observation instant explicitly;
    # its published_at is cleared because hold_notice_date has date-only precision.
    mapped = dict(row, end_date=day, knowledge_cutoff=knowledge)
    output = materialize_shareholder_count_row(
        mapped,
        company_id=company_id,
        security_id=security_id,
        source_sha256=source_sha256,
        parent_knowledge_from=parent_knowledge,
    )
    raw_average = output["raw_average_holding"]
    if raw_average is not None and output["average_holding"] != Decimal(raw_average):
        raise SourceAssessmentError("HOLDER_DECIMAL_PRECISION_CHANGED")
    output.update(knowledge_from=knowledge, published_at=None, source_revision=revision)
    return {"dwd_shareholder_count_observation": [output]}


def quarterly_observation(
    row: dict[str, Any],
    *,
    source: FinancialReportingSource,
    resolver: FinancialCompanyResolver,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Use the governed native financial adapter without its missing-clock fallback.

    Original publish/update strings remain source evidence. A date-only public
    observation is not upgraded to an actual publication instant in this revision.
    No cross-source values are coalesced, and unresolved company periods stay out.
    """
    clock = instant(row.get("acquired_at"))
    expanded = expand_financial_native_row(
        row,
        source=source,
        company_resolver=resolver,
        knowledge_cutoff=clock,
        through_date=THROUGH,
    )
    publication = row.get("publish_date")
    try:
        publication_instant = instant(publication)
    except SourceAssessmentError:
        publication_instant = None
    result = {
        "dwd_financial_report": list(expanded.reports),
        "dwd_financial_concept": list(expanded.concepts),
        "dwd_financial_fact": list(expanded.facts),
    }
    for name in ("dwd_financial_report", "dwd_financial_fact"):
        for output in result[name]:
            output["published_at"] = publication_instant
    return result, list(expanded.quality_exceptions)


def industry_snapshot_observation(
    row: dict[str, Any],
    *,
    company_id: str,
    node: Mapping[str, Any],
    parent_knowledge: datetime,
    source_sha256: str,
    revision: str,
) -> dict[str, list[dict[str, Any]]]:
    """A newly observed dated static snapshot; never backfill a historical industry."""
    day = dividend_date(row["market_date"])
    if day > THROUGH:
        return {}
    if str(row["industry_code"]) != node["node_code"]:
        raise SourceAssessmentError("INDUSTRY_NODE_SOURCE_CONFLICT")
    start = datetime.combine(day, time(), SHANGHAI).astimezone(timezone.utc)
    knowledge = acquisition(row, max(parent_knowledge, node["knowledge_from"]))
    if (
        node["effective_from"] > start
        or (node["effective_to"] is not None and start >= node["effective_to"])
        or (node["knowledge_to"] is not None and knowledge >= node["knowledge_to"])
    ):
        raise SourceAssessmentError("INDUSTRY_NODE_TIME_CONFLICT")
    return {
        "dwd_company_industry_membership": [
            {
                "record_source": "base22_weekly/industry_snapshot",
                "source_record_id": digest([row["stock_code"], day, row["industry_code"]]),
                "source_revision": revision,
                "source_artifact_sha256": source_sha256,
                "knowledge_from": knowledge,
                "knowledge_to": None,
                "effective_from": start,
                "effective_to": None,
                "company_id": company_id,
                "industry_node_id": node["industry_node_id"],
                "membership_type": "PRIMARY_CLASSIFICATION",
                "confidence": None,
                "source_declared_as_of": day,
            }
        ]
    }


def adjustment_observation(
    row: dict[str, Any],
    *,
    security_id: str,
    parent_knowledge: datetime,
    source_sha256: str,
    vocabulary: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """New actual acquisition observation; original unknown visibility remains unknown."""
    knowledge = acquisition(row, parent_knowledge)
    day = dividend_date(row["effective_date"])
    if day > THROUGH:
        return {}
    for field in vocabulary["factor_methods"]:
        value = row.get(field)
        if value not in (None, "") and (
            isinstance(value, float) or int(Decimal(str(value)).as_tuple().exponent) < -18
        ):
            raise SourceAssessmentError("FACTOR_DECIMAL_LOSS")
    mapped = dict(row, effective_date=day, knowledge_cutoff=knowledge)
    return materialize_adjustment_factor_rows(
        mapped,
        source_sha256=source_sha256,
        company_security_id=security_id,
        vocabulary=vocabulary,
        parent_knowledge_from=parent_knowledge,
    )


def st_observation(
    row: dict[str, Any],
    *,
    security_id: str,
    parent_knowledge: datetime,
    source_sha256: str,
    revision: str,
    warehouse_observed_at: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Governed ST mapping observed now; never infer original acquisition from built_at."""
    start_day = dividend_date(row["effective_start"])
    if start_day > THROUGH:
        return {}
    start = datetime.combine(start_day, time(), SHANGHAI).astimezone(timezone.utc)
    end_day = dividend_date(row["effective_end"]) if row.get("effective_end") else None
    end = (
        datetime.combine(end_day + timedelta(days=1), time(), SHANGHAI).astimezone(timezone.utc)
        if end_day
        else None
    )
    knowledge = max(instant(warehouse_observed_at), instant(parent_knowledge))
    flag = str(row["is_st"])
    if flag not in {"0", "1"}:
        raise SourceAssessmentError("ST_FLAG_UNKNOWN")
    label = str(row["status_label"])
    return {
        "dwd_security_status_interval": [
            {
                "record_source": "base22_weekly/st_status_retrospective",
                "source_record_id": str(row["interval_id"]),
                "source_revision": revision,
                "source_artifact_sha256": source_sha256,
                "knowledge_from": knowledge,
                "knowledge_to": None,
                "effective_from": start,
                "effective_to": end,
                "security_status_id": _stable_id(
                    "security_status", "SPECIAL_TREATMENT", security_id, start.isoformat()
                ),
                "security_id": security_id,
                "status_type": "SPECIAL_TREATMENT",
                "status_value": "UNKNOWN"
                if label == "unknown"
                else label.upper()
                if flag == "1"
                else "NORMAL",
                "reason_code": "BASELINE_NOT_PROVEN"
                if row["confidence"] == "not_proven"
                else row["event_kind"],
            }
        ]
    }


def decimal_observation(value: Any, *, nullable: bool = False) -> Decimal | None:
    """Exact shortest-decimal representation of retained numeric scalar.

    Source binary bytes remain evidence. Never quantize to make a provider value
    fit DECIMAL(38,10); an unsupported precision blocks the affected observation.
    This does not assert a missing original provider Decimal or raw acquisition.
    """
    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or value is None
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise SourceAssessmentError("NUMERIC_OBSERVATION_INVALID")
    result = Decimal(str(value))
    if not result.is_finite() or int(result.as_tuple().exponent) < -10:
        raise SourceAssessmentError("NUMERIC_OBSERVATION_PRECISION_UNSUPPORTED")
    return result


def market_observation(
    row: dict[str, Any],
    *,
    listing: Mapping[str, Any],
    calendar: Mapping[str, Any],
    source_sha256: str,
    revision: str,
    family: str,
) -> dict[str, list[dict[str, Any]]]:
    """Actual daily bar/status/valuation observation; no price/state repair priority.

    The declared source date, dated listing and same-market calendar must close.
    Existing Daily gates are untouched. A state is exactly the provider observation,
    never a flag flip to compensate for a missing bar.
    """
    day = dividend_date(row["date"])
    if day != dividend_date(row["market_date"]) or day > THROUGH:
        raise SourceAssessmentError("MARKET_SOURCE_BUSINESS_DATE_CONFLICT")
    start = datetime.combine(day, time(), SHANGHAI).astimezone(timezone.utc)
    if calendar["trade_date"] != day or calendar["venue_id"] != listing["venue_id"]:
        raise SourceAssessmentError("MARKET_CALENDAR_DATE_VENUE_CONFLICT")
    knowledge = acquisition(row, max(listing["knowledge_from"], calendar["knowledge_from"]))
    if listing["effective_from"] > start or (
        listing["effective_to"] is not None and start >= listing["effective_to"]
    ):
        raise SourceAssessmentError("MARKET_LISTING_DATE_CONFLICT")
    common = {
        "record_source": "base22_august/" + family,
        "source_record_id": f"stock_code={row['stock_code']}&date={day}",
        "source_revision": revision,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": knowledge,
        "knowledge_to": None,
        "observation_start": start,
        "observation_end": start + timedelta(days=1),
    }
    if family == "daily_ohlcv_none_delta":
        if row["adj_mode"] != "none":
            raise SourceAssessmentError("MARKET_UNADJUSTED_BASIS_UNKNOWN")
        return {
            "dwd_security_bar": [
                {
                    **common,
                    "listing_id": listing["listing_id"],
                    "calendar_session_id": calendar["calendar_session_id"],
                    "interval_size": "1d",
                    "price_basis": "UNADJUSTED",
                    "currency": listing["listing_currency"],
                    **{
                        field: decimal_observation(row[field])
                        for field in ["open", "high", "low", "close"]
                    },
                    "volume": decimal_observation(row.get("volume"), nullable=True),
                    "turnover": decimal_observation(row.get("amount"), nullable=True),
                    "trade_count": None,
                }
            ]
        }
    if family == "daily_status_observation_delta":
        if row["tradestatus"] not in {"0", "1"} or row["isST"] not in {"0", "1"}:
            raise SourceAssessmentError("MARKET_STATUS_UNKNOWN")
        tradable = row["tradestatus"] == "1"
        return {
            "dwd_tradability_state": [
                {
                    **common,
                    "listing_id": listing["listing_id"],
                    "calendar_session_id": calendar["calendar_session_id"],
                    "is_tradable": tradable,
                    "can_buy": tradable,
                    "can_sell": tradable,
                    "reason_code": "SOURCE_NOT_TRADABLE"
                    if not tradable
                    else "ST_TRADABLE"
                    if row["isST"] == "1"
                    else "NORMAL",
                    "upper_price": None,
                    "lower_price": None,
                    "input_revision_digest": digest([revision, source_sha256]),
                }
            ]
        }
    if family == "valuation_daily_delta":
        return {
            "dwd_security_valuation": [
                {
                    **common,
                    "security_id": listing["security_id"],
                    "listing_id": listing["listing_id"],
                    "measure_type": field,
                    "measure_value": decimal_observation(row.get(field), nullable=True),
                    "currency": None,
                    "methodology": "provider_reported",
                    "fiscal_basis": "ttm_or_reported",
                }
                for field in ["pe_ttm", "pb", "ps_ttm", "pcf_ttm"]
            ]
        }
    raise SourceAssessmentError("MARKET_NONEMPTY_FAMILY_UNHANDLED")


def calendar_observation(
    row: dict[str, Any],
    *,
    venue: Mapping[str, Any],
    source_sha256: str,
    revision: str,
    warehouse_observed_at: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """New retrospective source calendar claim, preserving unknown original acquisition.

    Source built_at is archived, not backdated into provider availability. This
    adapter retains the existing regular-day hours only for the governed SSE/SZSE
    exchange calendar; BSE authority remains a separate mandatory gate.
    """
    day = dividend_date(row["trade_date"])
    if day > THROUGH:
        return {}
    if row["exchange"] not in {"SSE", "SZSE"} or row["is_open"] not in {0, 1}:
        raise SourceAssessmentError("CALENDAR_AUTHORITY_OR_OPEN_STATUS_UNQUALIFIED")
    expected = {"SSE": "XSHG", "SZSE": "XSHE"}[row["exchange"]]
    if venue["mic_code"] != expected:
        raise SourceAssessmentError("CALENDAR_VENUE_CONFLICT")
    start = datetime.combine(day, time(), SHANGHAI).astimezone(timezone.utc)
    knowledge = max(instant(warehouse_observed_at), instant(venue["knowledge_from"]))
    return {
        "dwd_trading_calendar": [
            {
                "record_source": "base22_august/calendar_retrospective",
                "source_record_id": (
                    f"exchange={row['exchange']}&trade_date={day}&session=REGULAR_DAY"
                ),
                "source_revision": revision,
                "source_artifact_sha256": source_sha256,
                "knowledge_from": knowledge,
                "knowledge_to": None,
                "observation_start": start,
                "observation_end": start + timedelta(days=1),
                "calendar_session_id": _stable_id(
                    "calendar_session", venue["venue_id"], str(day), "REGULAR_DAY"
                ),
                "venue_id": venue["venue_id"],
                "trade_date": day,
                "session_type": "REGULAR_DAY",
                "is_open": bool(row["is_open"]),
                "open_at": datetime.combine(day, time(9, 30), SHANGHAI).astimezone(timezone.utc)
                if row["is_open"]
                else None,
                "close_at": datetime.combine(day, time(15), SHANGHAI).astimezone(timezone.utc)
                if row["is_open"]
                else None,
                "calendar_reason": None if row["is_open"] else "SCHEDULED_CLOSED",
            }
        ]
    }


def knowledge_versions(
    rows: Iterable[dict[str, Any]],
    rule: dict[str, Any],
    *,
    revision_entity_key: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Order real body revisions; identical observations retain separate source evidence.

    Caller records every original source row before invoking this bounded-group
    adapter. Equal-clock, different-body conflicts fail rather than choose a source.
    """
    groups: dict[str, dict[datetime, dict[str, Any]]] = {}
    excluded = {
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
    }
    for row in rows:
        key = digest([row[k] for k in (revision_entity_key or rule["business_key"])])
        clock = instant(row["knowledge_from"])
        body = digest({k: v for k, v in row.items() if k not in excluded})
        group = groups.setdefault(key, {})
        if clock in group:
            old_body = digest({k: v for k, v in group[clock].items() if k not in excluded})
            if old_body != body:
                raise SourceAssessmentError("OBSERVATION_EQUAL_CLOCK_BODY_CONFLICT")
            # Deterministic provenance representative; all observations stay in evidence.
            group[clock] = min((group[clock], row), key=digest)
        else:
            group[clock] = dict(row)
    output = []
    for key in sorted(groups):
        ordered: list[dict[str, Any]] = []
        for clock in sorted(groups[key]):
            row = groups[key][clock]
            body = digest({k: v for k, v in row.items() if k not in excluded})
            if ordered and body == digest(
                {k: v for k, v in ordered[-1].items() if k not in excluded}
            ):
                continue
            ordered.append(row)
        for i, row in enumerate(ordered):
            row["knowledge_to"] = ordered[i + 1]["knowledge_from"] if i + 1 < len(ordered) else None
            output.append(row)
    return output


def financial_concept_versions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the approved cumulative earliest-period concept rule within a batch.

    This is the same revision predicate as the governed financial builder: an
    observation adds a concept revision only when its source period extends the
    cumulative effective scope backwards. Cross-batch/native closure is still
    mandatory before a complete image can be sealed.
    """
    semantic_fields = [
        "taxonomy_namespace",
        "taxonomy_version",
        "concept_code",
        "concept_name",
        "statement_type",
        "data_type",
        "period_type",
        "balance_type",
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["financial_concept_id"], []).append(row)
    result = []
    for key in sorted(grouped):
        group = grouped[key]
        if len({digest([r[k] for k in semantic_fields]) for r in group}) != 1:
            raise SourceAssessmentError("FINANCIAL_CONCEPT_SOURCE_SEMANTICS_CONFLICT")
        by_clock: dict[datetime, dict[str, Any]] = {}

        def order(r: dict[str, Any]) -> tuple[Any, ...]:
            return (
                r["effective_from"],
                r["source_record_id"],
                r["source_artifact_sha256"],
                r["record_source"],
                r["source_revision"],
            )

        for row in group:
            clock = instant(row["knowledge_from"])
            if clock not in by_clock or order(row) < order(by_clock[clock]):
                by_clock[clock] = dict(row)
        revisions = []
        earliest = None
        for clock in sorted(by_clock):
            row = by_clock[clock]
            if earliest is None or row["effective_from"] < earliest:
                earliest = row["effective_from"]
                revisions.append(row)
        for i, row in enumerate(revisions):
            row["knowledge_to"] = (
                revisions[i + 1]["knowledge_from"] if i + 1 < len(revisions) else None
            )
            result.append(row)
    return result


def parent_available_versions(
    observations: Iterable[tuple[dict[str, Any], datetime]],
    rule: dict[str, Any],
    *,
    revision_entity_key: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep source revisions when a required identity becomes available later.

    Callers archive all original typed source observations in the job. Several
    pre-parent bodies cannot become distinct DWD versions at one instant: the
    latest genuinely acquired body applies at first identity availability. No
    earlier identity clock or artificial microsecond offsets are invented.
    """
    groups: dict[str, list[tuple[dict[str, Any], datetime]]] = {}
    for row, clock in observations:
        clock = instant(clock)
        if clock > row["knowledge_from"]:
            raise SourceAssessmentError("SOURCE_ACQUISITION_AFTER_OUTPUT_KNOWLEDGE")
        key = digest(
            [row[k] for k in (revision_entity_key or rule["business_key"])]
            + [row["knowledge_from"]]
        )
        groups.setdefault(key, []).append((row, clock))
    selected = []
    folded = []
    excluded = {
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
    }
    for key in sorted(groups):
        group = groups[key]
        per_clock: dict[datetime, set[str]] = {}
        for row, clock in group:
            per_clock.setdefault(clock, set()).add(
                digest({k: v for k, v in row.items() if k not in excluded})
            )
        if any(len(bodies) > 1 for bodies in per_clock.values()):
            raise SourceAssessmentError("OBSERVATION_EQUAL_SOURCE_CLOCK_CONFLICT")
        latest = max(clock for _, clock in group)
        chosen = min((row for row, clock in group if clock == latest), key=digest)
        selected.append(chosen)
        if len(set.union(*per_clock.values())) > 1:
            folded.append(
                {
                    "version_key_sha256": key,
                    "parent_available_at": chosen["knowledge_from"].isoformat(),
                    "latest_real_acquisition": latest.isoformat(),
                    "original_observations": [
                        {"source_acquired_at": clock.isoformat(), "row_sha256": digest(row)}
                        for row, clock in group
                    ],
                    "disposition": "EARLIER_SOURCE_BODIES_ARCHIVED; NO_DWD_PRE_PARENT_AVAILABILITY",
                }
            )
    return knowledge_versions(selected, rule, revision_entity_key=revision_entity_key), folded


def write_partition(
    rows: Iterable[dict[str, Any]],
    *,
    table: str,
    expected_rows: int,
    expected_partition: str,
    schema: Any,
    rule: dict[str, Any],
    admitted: set[tuple[str, str, str]],
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    """Exclusive typed Parquet with exact pre-frozen partition/count and safe digest.

    One bounded partition at a time; no full-history Python/JSON/SQLite materialization.
    A failed write retains its partial file. No image manifest or READY is emitted.
    """
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    output = budget.destination(output)
    partial = output.with_suffix(output.suffix + ".partial")
    if output.exists() or partial.exists():
        raise BoundedError("OBSERVATION_OUTPUT_EXISTS")
    budget.check()
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    keys = set()  # bounded single partition; global verifier remains a separate gate
    buffer = []
    with (
        partial.open("xb") as stream,
        pq.ParquetWriter(stream, schema, compression="zstd") as writer,
    ):
        for row in rows:
            _check_row(row, rule, schema, admitted)
            if partition(row, rule) != expected_partition:
                raise BoundedError("OBSERVATION_PARTITION_MISMATCH")
            key = digest([row[k] for k in rule["version_key"]])
            if key in keys:
                raise BoundedError("OBSERVATION_DUPLICATE_VERSION")
            keys.add(key)
            buffer.append(row)
            count += 1
            if count > expected_rows:
                raise BoundedError("OBSERVATION_ROW_CONSERVATION_FAILED")
            if len(buffer) >= budget.limits.batch_rows:
                writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                buffer.clear()
                budget.check()
        if buffer:
            writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
        if count != expected_rows:
            raise BoundedError("OBSERVATION_ROW_CONSERVATION_FAILED")
    # Read every typed column back before sealing this partial observation output.
    scan = semantic_scan([partial], schema, batch_rows=budget.limits.batch_rows, check=budget.check)
    if scan["rows"] != expected_rows:
        raise BoundedError("OBSERVATION_SERIALIZATION_CENSUS_FAILED")
    with partial.open("rb") as reader:
        os.fsync(reader.fileno())
    os.link(partial, output)  # only owned files; unlink leaves an independent nlink=1
    partial.unlink()
    budget.check()
    return {
        "table": table,
        "partition": expected_partition,
        "rows": count,
        "sha256": file_sha(output),
        "size_bytes": output.stat().st_size,
        "allocated_bytes": output.stat().st_blocks * 512,
        "semantic_scan": scan,
        "version_key_census_sha256": digest(sorted(keys)),
        "status": "SEALED_OBSERVATIONS_NOT_COMPLETE_IMAGE",
    }
