"""Exact native source qualification for a later P6 context, never activation.

This is a successor to the diagnostic seven-table handoff. Late source knowledge
stays late. Held keys remain physical output rows with reason codes; they do not
invalidate independent qualified keys or create paper/execution authority.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import os
import re
import shutil
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from quanthus.backtest.base24_semantic_projection import NATIVE_KEYS
from quanthus.price_limits import resolve_price_limit_policy

from . import base24_p6_handoff as source
from .base22 import file_sha
from .base22_sources import _fingerprint, _tree, disjoint_output, physical_path

VERSION = "quanthus.p10-base24-p6-purpose-qualification.v1"
CONTRACT: dict[str, Any] = {
    "schema_version": VERSION,
    "purpose": "later_snapshot_source_qualification",
    "historical_pit_granted": False,
    "paper_entry_granted": False,
    "execution_authorized": False,
    "native_units": "cash_per_share_and_explicit_share_ratios_no_factor_unit_inference",
    "ipo_window": "exact_original_rule_duration_and_observed_provider_trading_sessions",
    "unknown_or_conflicting": "hold_affected_key_preserve_native_values",
    "status_freshness": "exact_dated_provider_observation_not_open_interval_persistence",
    "company_action_day_execution": "hold_until_exact_reference_price_qualified",
    "policy_changes": False,
}
POLICY_HASHES = {
    "price-limit.v2.json": (
        "sha256:4ab1f49adea695d4361fe0652f4d5a4485d212c89e2035b50985615b37739c2b"
    ),
    "p1-corporate-action-normalization.v3.json": (
        "sha256:d3c8e2a9a16c642fa65ef5c3a319cdfc940f7b6a8b0b30b6569159586b012010"
    ),
}
_IPO_RULES = {
    "ipo_first_5_trading_days_no_limit",
    "ipo_main_first_trading_day_44pct",
    "ipo_main_first_day_legacy_special",
    "ipo_legacy_main_first_day_special_not_modeled_full",
}
_TERMS = {"CASH_AMOUNT_PER_SHARE", "BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE"}


class PurposeError(ValueError):
    """A stable qualification or immutable-output failure."""


def resolve_status(canonical: list[str], provider: list[str]) -> tuple[str | None, str]:
    def known(values: list[str]) -> set[str]:
        if any(v not in {"NORMAL", "ST", "DELISTING_RISK", "UNKNOWN"} for v in values):
            return {"INVALID"}
        return {"ST" if v == "DELISTING_RISK" else v for v in values if v != "UNKNOWN"}

    left, right = known(canonical), known(provider)
    if len(left | right) > 1 or "INVALID" in left | right:
        return None, "STATUS_CONFLICT"
    if left:
        return next(iter(left)), "CANONICAL_STATE"
    if right:
        return next(iter(right)), "PROVIDER_STATE"
    return None, "STATUS_UNKNOWN"


def ipo_window(rule: str, duration: Any, ordinal: int, complete: bool) -> bool:
    """A lower-bound count proves exit; initial membership needs complete history."""
    if rule not in _IPO_RULES:
        raise PurposeError("IPO_RULE")
    if rule == "ipo_legacy_main_first_day_special_not_modeled_full":
        raise PurposeError("IPO_UNMODELED_WINDOW")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float, Decimal))
        or not math.isfinite(duration)
        or duration <= 0
        or duration != int(duration)
    ):
        raise PurposeError("IPO_DURATION")
    if ordinal > int(duration):
        return False
    if not complete:
        raise PurposeError("IPO_COUNT_INCOMPLETE")
    return True


def _nonnegative(value: Any) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise PurposeError("ACTION_VALUE")
    return value


def action_terms(action_type: str, terms: list[dict[str, Any]]) -> tuple[Decimal, Decimal]:
    """Native per-share columns are authoritative units; never infer a /10 fit."""
    if not terms:
        raise PurposeError("ACTION_TERM_MISSING")
    if action_type not in {"CASH_DIVIDEND", "CAPITAL_RESERVE_TRANSFER", "MIXED_DISTRIBUTION"}:
        raise PurposeError("ACTION_TYPE")
    values: dict[str, Decimal] = {}
    for row in terms:
        kind = row["term_type"]
        if kind not in _TERMS:
            raise PurposeError("ACTION_TERM")
        if kind in values or row.get("option_id") is not None:
            raise PurposeError("ACTION_TERM_AMBIGUOUS")
        if kind == "CASH_AMOUNT_PER_SHARE":
            if row.get("currency") != "CNY":
                raise PurposeError("ACTION_CURRENCY")
            values[kind] = _nonnegative(row.get("amount_per_share"))
        else:
            numerator = _nonnegative(row.get("ratio_numerator"))
            denominator = _nonnegative(row.get("ratio_denominator"))
            if denominator == 0:
                raise PurposeError("ACTION_RATIO")
            with localcontext() as ctx:
                ctx.prec = 60
                ratio = numerator / denominator
                if int(ratio.as_tuple().exponent) < -18:
                    raise PurposeError("ACTION_RATIO_PRECISION")
                values[kind] = ratio
    if action_type in {"CASH_DIVIDEND", "MIXED_DISTRIBUTION"} and not values.get(
        "CASH_AMOUNT_PER_SHARE", Decimal(0)
    ):
        raise PurposeError("ACTION_TERM_MISSING")
    if action_type in {"CAPITAL_RESERVE_TRANSFER", "MIXED_DISTRIBUTION"} and not (
        values.get("BONUS_SHARES_PER_SHARE", Decimal(0))
        + values.get("TRANSFER_SHARES_PER_SHARE", Decimal(0))
    ):
        raise PurposeError("ACTION_TERM_MISSING")
    return (
        values.get("CASH_AMOUNT_PER_SHARE", Decimal(0)),
        values.get("BONUS_SHARES_PER_SHARE", Decimal(0))
        + values.get("TRANSFER_SHARES_PER_SHARE", Decimal(0)),
    )


def factor_check(
    prior: Decimal | None,
    before: Decimal | None,
    after: Decimal | None,
    cash: Decimal,
    shares: Decimal,
    tolerance: Decimal,
) -> tuple[str, Decimal | None, Decimal | None, Decimal | None]:
    if any(v is None for v in (prior, before, after)):
        return "unavailable", None, None, None
    assert prior is not None and before is not None and after is not None
    if any(not v.is_finite() or v <= 0 for v in (prior, before, after)) or prior <= cash:
        return "mismatch", None, None, None
    with localcontext() as ctx:
        ctx.prec = 60
        observed = after / before
        expected = (1 + shares) * prior / (prior - cash)
        error = abs(observed - expected)
        quantum = Decimal("0.000000000000000001")
        audit = tuple(
            v.quantize(quantum, rounding=ROUND_HALF_UP) for v in (observed, expected, error)
        )
    return ("matched" if error <= tolerance else "mismatch"), audit[0], audit[1], audit[2]


def _rows(c: Any, query: str) -> list[dict[str, Any]]:
    cursor = c.execute(query)
    names = [v[0] for v in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _digest(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _latest(c: Any, root: Path, cutoff: str) -> None:
    visibility = (
        f"(knowledge_from<=TIMESTAMPTZ {source._q(cutoff)} OR knowledge_from IS NULL) AND "
        f"coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ {source._q(cutoff)}"
    )
    keys = dict(NATIVE_KEYS)
    keys["dwd_security_status_interval"] = "security_status_id,effective_from"
    for name in source.NATIVE:
        raw = root / "native" / (name + ".parquet")
        c.execute(
            f"CREATE VIEW p_{name} AS SELECT *,count(*) OVER "
            f"(PARTITION BY {keys[name]}) AS visible_versions FROM "
            f"(SELECT DISTINCT * FROM read_parquet({source._q(raw)}) WHERE {visibility}) "
            f"QUALIFY row_number() OVER (PARTITION BY {keys[name]} "
            "ORDER BY knowledge_from DESC)=1"
        )


def _action_rows(c: Any, policy: dict[str, Any]) -> list[dict[str, Any]]:
    terms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for term_row in _rows(c, "SELECT * FROM p_dwd_corporate_action_term"):
        terms[term_row["corporate_action_id"]].append(term_row)
    actions = _rows(c, "SELECT * FROM p_dwd_corporate_action ORDER BY corporate_action_id")
    # Reference selection uses an actual interval containing the event, not the
    # last stale factor or a future observation. All selected source clocks are
    # bounded by the separately declared later snapshot cutoff.
    refs = _rows(
        c,
        """SELECT a.corporate_action_id,
          (SELECT CASE WHEN count(*)=1 THEN min(b.close) END
           FROM p_dwd_security_bar b JOIN p_dwd_listing l USING(listing_id)
           WHERE l.security_id=a.security_id AND b.visible_versions=1
           AND CAST(b.observation_start AS DATE)=
             (SELECT max(CAST(q.observation_start AS DATE))
              FROM p_dwd_security_bar q JOIN p_dwd_listing z USING(listing_id)
              WHERE z.security_id=a.security_id AND q.close>0
                AND CAST(q.observation_start AS DATE)<CAST(a.effective_at AS DATE))) prior_close,
          (SELECT max(b.knowledge_from) FROM p_dwd_security_bar b
           JOIN p_dwd_listing l USING(listing_id) WHERE l.security_id=a.security_id
           AND CAST(b.observation_start AS DATE)<CAST(a.effective_at AS DATE)) bar_known,
          (SELECT CASE WHEN count(*)=1 THEN min(f.factor_value) END
           FROM p_dwd_adjustment_factor f WHERE f.security_id=a.security_id
           AND f.adjustment_direction='BACKWARD' AND f.adjustment_method='BACK_ADJUSTMENT'
           AND f.visible_versions=1 AND f.effective_from<a.effective_at
           AND coalesce(f.effective_to,'infinity'::TIMESTAMPTZ)>=a.effective_at) factor_before,
          (SELECT CASE WHEN count(*)=1 THEN min(f.factor_value) END
           FROM p_dwd_adjustment_factor f WHERE f.security_id=a.security_id
           AND f.adjustment_direction='BACKWARD' AND f.adjustment_method='BACK_ADJUSTMENT'
           AND f.visible_versions=1 AND f.effective_from<=a.effective_at
           AND coalesce(f.effective_to,'infinity'::TIMESTAMPTZ)>a.effective_at) factor_after,
          (SELECT max(f.knowledge_from) FROM p_dwd_adjustment_factor f
           WHERE f.security_id=a.security_id AND f.adjustment_direction='BACKWARD'
           AND f.adjustment_method='BACK_ADJUSTMENT' AND f.effective_from<=a.effective_at)
           factor_known
          FROM p_dwd_corporate_action a""",
    )
    reference = {r["corporate_action_id"]: r for r in refs}
    grouped: Counter[tuple[Any, Any]] = Counter(
        (r["security_id"], r["ex_date"] or r["effective_at"].date())
        for r in actions
        if r["action_status"] == "IMPLEMENTED"
    )
    result = []
    for action in actions:
        ident = action["corporate_action_id"]
        native_terms = terms[ident]
        knowledge = [r["knowledge_from"] for r in [action, *native_terms]]
        row: dict[str, Any] = dict(
            action_id=ident,
            security_id=action["security_id"],
            effective_at=action["effective_at"],
            ex_date=action["ex_date"],
            action_status=action["action_status"],
            native_knowledge_from=action["knowledge_from"],
            qualified_knowledge_from=max(knowledge) if all(knowledge) else None,
            cash_per_share=None,
            share_ratio=None,
            cash_unit=None,
            factor_ratio_observed=None,
            factor_ratio_expected=None,
            factor_ratio_error=None,
            factor_cross_check_status="unavailable",
            qualified=False,
            reason_code="ACTION_NOT_IMPLEMENTED",
            evidence_digest=_digest([action, native_terms]),
            execution_authorized=False,
        )
        if action["action_status"] != "IMPLEMENTED":
            result.append(row)
            continue
        try:
            if any(r["visible_versions"] != 1 for r in [action, *native_terms]):
                raise PurposeError("ACTION_VERSION_AMBIGUOUS")
            if any(t["effective_at"] != action["effective_at"] for t in native_terms):
                raise PurposeError("ACTION_TERM_EFFECTIVE_DATE")
            if (
                grouped[(action["security_id"], action["ex_date"] or action["effective_at"].date())]
                != 1
            ):
                raise PurposeError("ACTION_EVENT_AMBIGUOUS")
            if action["ex_date"] is None or action["ex_date"] != action["effective_at"].date():
                raise PurposeError("ACTION_EFFECTIVE_DATE")
            cash, shares = action_terms(action["action_type"], native_terms)
            row.update(cash_per_share=cash, share_ratio=shares, cash_unit="per_share")
            ref = reference[ident]
            if not all([*knowledge, ref["bar_known"], ref["factor_known"]]):
                raise PurposeError("ACTION_KNOWLEDGE_UNKNOWN")
            row["qualified_knowledge_from"] = max(*knowledge, ref["bar_known"], ref["factor_known"])
            status, observed, expected, error = factor_check(
                ref["prior_close"],
                ref["factor_before"],
                ref["factor_after"],
                cash,
                shares,
                Decimal(policy["maximum_factor_ratio_error"]),
            )
            row.update(
                factor_cross_check_status=status,
                factor_ratio_observed=observed,
                factor_ratio_expected=expected,
                factor_ratio_error=error,
                evidence_digest=_digest([action, native_terms, ref]),
                qualified=status == "matched",
                reason_code="ACTION_MATCHED"
                if status == "matched"
                else "ACTION_FACTOR_" + status.upper(),
            )
        except PurposeError as exc:
            row["reason_code"] = str(exc)
        result.append(row)
    return result


def _dated_status(c: Any, root: Path, cutoff: str) -> None:
    """Only an actual dated observation can attest provider state on that day."""
    selected = source._read(root / "evidence/status-selection.json")
    if any(
        not isinstance(sha, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", sha)
        for sha in selected
    ):
        raise PurposeError("STATUS_LOCAL_SELECTION")
    paths = [root / "evidence/status" / (sha[7:] + ".parquet") for sha in selected]
    if not paths:
        c.execute("""CREATE VIEW dated_status AS SELECT NULL::VARCHAR stock_code,
          NULL::DATE market_date,NULL::VARCHAR status_value,NULL::VARCHAR reason,
          NULL::TIMESTAMPTZ known,NULL::VARCHAR evidence WHERE false""")
        return
    inventory = root / "evidence/status-original-manifest.json"
    inventory_hash = file_sha(inventory)
    members = source._read(inventory)["content"]["files"]
    for sha, entry in selected.items():
        path = root / "evidence/status" / (sha[7:] + ".parquet")
        if file_sha(path) != sha or entry["inventory_sha256"] != inventory_hash:
            raise PurposeError("STATUS_LOCAL_EVIDENCE_HASH")
        if not entry["original_members"] or any(
            m not in members for m in entry["original_members"]
        ):
            raise PurposeError("STATUS_LOCAL_INVENTORY")
        if any(
            m["sha256"].removeprefix("sha256:") != sha[7:] or m["size_bytes"] != path.stat().st_size
            for m in entry["original_members"]
        ):
            raise PurposeError("STATUS_LOCAL_MEMBERSHIP")
    files = ",".join(source._q(p) for p in paths)
    # Keep equal-time conflicting revisions; do not choose a winner by filename.
    c.execute(
        f"""CREATE VIEW dated_status AS
        WITH raw AS (SELECT DISTINCT stock_code,try_cast(market_date AS DATE) market_date,
          CASE isST WHEN '1' THEN 'ST' WHEN '0' THEN 'NORMAL' ELSE 'UNKNOWN' END status_value,
          CASE tradestatus WHEN '1' THEN 'PROVIDER_TRADING'
            WHEN '0' THEN 'PROVIDER_NOT_TRADING' ELSE 'UNKNOWN' END reason,
          CASE WHEN regexp_matches(acquired_at,'(Z|[+-][0-9]{{2}}:[0-9]{{2}})$')
            THEN try_cast(acquired_at AS TIMESTAMPTZ) END known,
          filename FROM read_parquet([{files}],filename=true)),
        visible AS (SELECT * FROM raw WHERE known<=TIMESTAMPTZ {source._q(cutoff)}),
        latest AS (SELECT *,max(known) OVER (PARTITION BY stock_code,market_date) latest
          FROM visible)
        SELECT stock_code,market_date,status_value,reason,known,
          regexp_extract(min(filename),'([^/]+)[.]parquet$',1) evidence
          FROM latest WHERE known=latest
        GROUP BY stock_code,market_date,status_value,reason,known"""
    )


def _market_query(root: Path, start: date, end: date) -> str:
    """One narrow monthly join shared by quality and execution consumers."""
    bar = source._q(root / "tables/daily_ohlcv_none.parquet")
    universe = source._q(root / "tables/daily_universe.parquet")
    return f"""SELECT u.dwd_listing_id listing_id,u.dwd_security_id security_id,
      u.security_id listing_code,u.exchange,u.market_date,b.observation_start,
      b.dwd_close_decimal AS "close",b.dwd_open_decimal AS "open",b.dwd_high_decimal AS high,
      b.dwd_low_decimal AS low,b.dwd_volume_decimal AS volume,b.dwd_turnover_decimal AS amount,
      b.dwd_bar_knowledge_from bar_known,b.dwd_bar_knowledge_to bar_knowledge_to,
      u.is_tradable,u.can_buy,u.can_sell,u.reason_code state_reason,
      (SELECT list(struct_pack(value:=x.status_value,reason:=x.reason,
         known:=x.known,evidence:=x.evidence)) FROM dated_status x
       WHERE x.stock_code=u.security_id AND x.market_date=u.market_date) dated_status,
      (SELECT CASE WHEN count(*)=1 AND min(l.visible_versions)=1 THEN min(l.board_code) END
       FROM p_dwd_listing l WHERE l.listing_id=u.dwd_listing_id
       AND l.effective_from<=b.observation_start
       AND coalesce(l.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) board,
      (SELECT count(*) FROM p_dwd_listing l WHERE l.listing_id=u.dwd_listing_id
       AND l.visible_versions=1 AND l.effective_from<=b.observation_start
       AND coalesce(l.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) listing_count,
      (SELECT count(*) FROM p_dwd_security x WHERE x.security_id=u.dwd_security_id
       AND x.visible_versions=1 AND x.effective_from<=b.observation_start
       AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) security_count,
      (SELECT list(struct_pack(kind:=x.status_type,value:=x.status_value,
         reason:=x.reason_code,versions:=x.visible_versions,known:=x.knowledge_from,
         evidence:=x.source_artifact_sha256)) FROM p_dwd_security_status_interval x
       WHERE x.security_id=u.dwd_security_id AND x.effective_from<=b.observation_start
       AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) statuses,
      (SELECT list(struct_pack(kind:=x.status_type,value:=x.status_value,
         versions:=x.visible_versions,known:=x.knowledge_from))
       FROM p_dwd_listing_status_interval x WHERE x.listing_id=u.dwd_listing_id
       AND x.effective_from<=b.observation_start
       AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) lifecycle,
      (SELECT list(struct_pack(id:=x.price_limit_regime_id,priority:=x.priority,
         kind:=x.regime_type,status:=x.security_status,ipo:=x.ipo_phase,
         upper_ratio:=x.upper_limit_ratio,lower_ratio:=x.lower_limit_ratio,
         rule:=r.listing_window_rule,duration:=r.no_limit_days,
         start_date:=r.effective_start,versions:=x.visible_versions,
         raw_evidence:=x.source_artifact_sha256,known:=x.knowledge_from))
       FROM p_dwd_price_limit_regime x LEFT JOIN raw_ipo r
        ON x.price_limit_regime_id='price_limit_regime_'||substr(sha256(r.interval_id),1,32)
       WHERE x.venue_id=u.dwd_venue_id AND (x.listing_id IS NULL OR x.listing_id=u.dwd_listing_id)
       AND (x.board_code IS NULL OR x.board_code=(SELECT min(l.board_code)
         FROM p_dwd_listing l WHERE l.listing_id=u.dwd_listing_id
         AND l.visible_versions=1 AND l.effective_from<=b.observation_start
         AND coalesce(l.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start))
       AND x.effective_from<=b.observation_start
       AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) regimes,
      t.upper_price,t.lower_price,t.visible_versions state_versions,
      t.knowledge_from state_known,
      (SELECT count(*) FROM p_dwd_security_bar x WHERE x.listing_id=u.dwd_listing_id
       AND x.calendar_session_id=b.calendar_session_id AND x.visible_versions=1) bar_count,
      (SELECT count(*) FROM p_dwd_trading_calendar x
       WHERE x.calendar_session_id=b.calendar_session_id AND x.visible_versions=1) calendar_count,
      (SELECT count(*) FROM p_dwd_trading_venue x WHERE x.venue_id=u.dwd_venue_id
       AND x.visible_versions=1 AND x.effective_from<=b.observation_start
       AND coalesce(x.effective_to,'infinity'::TIMESTAMPTZ)>b.observation_start) venue_count
      FROM read_parquet({universe}) u LEFT JOIN read_parquet({bar}) b
       ON b.dwd_listing_id=u.dwd_listing_id AND b.date=u.market_date
      LEFT JOIN p_dwd_tradability_state t ON t.listing_id=u.dwd_listing_id
       AND t.calendar_session_id=b.calendar_session_id
      WHERE u.market_date>=DATE '{start}' AND u.market_date<DATE '{end}'
      ORDER BY u.market_date,u.dwd_listing_id"""


def _state_row(
    row: dict[str, Any],
    *,
    ordinal: int,
    complete: bool,
    prior: Decimal | None,
    policy: dict[str, Any],
    raw_sha: str,
) -> dict[str, Any]:
    """Qualify original state; never change source flags, bounds, or missing bars."""
    statuses, lifecycle, regimes = (
        row.get("statuses") or [],
        row.get("lifecycle") or [],
        row.get("regimes") or [],
    )
    dated = row.get("dated_status") or []
    status, status_basis = resolve_status(
        [s["value"] for s in statuses if s["kind"] == "SPECIAL_TREATMENT"],
        [s["value"] for s in dated],
    )
    result: dict[str, Any] = dict(
        listing_id=row["listing_id"],
        security_id=row["security_id"],
        market_date=row["market_date"],
        status=status,
        status_basis=status_basis,
        ipo_rule=None,
        in_ipo_window=None,
        selected_regime_id=None,
        source_is_tradable=row["is_tradable"],
        source_can_buy=row["can_buy"],
        source_can_sell=row["can_sell"],
        source_upper_price=row["upper_price"],
        source_lower_price=row["lower_price"],
        warmup_eligible=False,
        execution_source_qualified=False,
        qualified_entry_allowed=False,
        reason_code="MARKET_OBSERVATION_MISSING",
        evidence_digest=_digest(row),
        execution_authorized=False,
    )
    try:
        if row["listing_code"] in policy["excluded_security_ids"] or any(
            row["listing_code"].startswith(p) for p in policy["out_of_scope_code_prefixes"]
        ):
            raise PurposeError("FROZEN_POLICY_OUT_OF_SCOPE")
        prices = [row[k] for k in ("open", "high", "low", "close")]
        if any(v is None for v in prices):
            raise PurposeError("MARKET_OBSERVATION_MISSING")
        if any(v <= 0 or not v.is_finite() for v in prices):
            raise PurposeError("MARKET_PRICE_INVALID")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(
            row["open"], row["close"]
        ):
            raise PurposeError("MARKET_RANGE_INVALID")
        if row["listing_count"] != 1 or row["security_count"] != 1:
            raise PurposeError("IDENTITY_VERSION_AMBIGUOUS")
        if row["state_versions"] != 1:
            raise PurposeError("STATE_VERSION_AMBIGUOUS")
        if any(row[k] != 1 for k in ("bar_count", "calendar_count", "venue_count")):
            raise PurposeError("CORE_VERSION_AMBIGUOUS")
        if row.get("bar_known") is None or row.get("state_known") is None:
            raise PurposeError("MARKET_KNOWLEDGE_UNKNOWN")
        if any(
            row[k] is None or not row[k].is_finite() or row[k] < 0 for k in ("volume", "amount")
        ):
            raise PurposeError("MARKET_ACTIVITY_INVALID")
        result["warmup_eligible"] = True
        if len(dated) != 1 or dated[0].get("known") is None:
            raise PurposeError("DATED_STATUS_UNAVAILABLE_OR_AMBIGUOUS")
        if dated[0]["reason"] not in {"PROVIDER_TRADING", "PROVIDER_NOT_TRADING"}:
            raise PurposeError("DATED_STATUS_UNKNOWN")
        expected_trading = dated[0]["reason"] == "PROVIDER_TRADING"
        if row["is_tradable"] != expected_trading:
            raise PurposeError("DATED_TRADING_STATE_CONFLICT")
        if row["is_tradable"] is True and (row["volume"] == 0 or row["amount"] == 0):
            raise PurposeError("TRADABLE_ZERO_ACTIVITY")
        if any(s["versions"] != 1 for s in [*statuses, *lifecycle]):
            raise PurposeError("STATUS_VERSION_AMBIGUOUS")
        if any(s.get("known") is None for s in [*statuses, *lifecycle]):
            raise PurposeError("STATUS_KNOWLEDGE_UNKNOWN")
        if status is None:
            raise PurposeError(status_basis)
        if not any(s["kind"] == "LISTING_LIFECYCLE" and s["value"] == "LISTED" for s in lifecycle):
            raise PurposeError("LISTING_LIFECYCLE_UNKNOWN")
        if any(s["value"] == "SUSPENDED" for s in lifecycle) and any(
            row[k] is True for k in ("is_tradable", "can_buy", "can_sell")
        ):
            raise PurposeError("SUSPENSION_STATE_CONFLICT")
        ipo = [r for r in regimes if r["ipo"] is not None]
        if len(ipo) != 1 or ipo[0]["raw_evidence"] != raw_sha or ipo[0]["versions"] != 1:
            raise PurposeError("IPO_SOURCE_CLOSURE")
        initial = ipo_window(ipo[0]["rule"], ipo[0]["duration"], ordinal, complete)
        result.update(ipo_rule=ipo[0]["rule"], in_ipo_window=initial)
        eligible = [
            r
            for r in regimes
            if r["versions"] == 1
            and (r["status"] is None or r["status"] == status)
            and (r["ipo"] is None or initial)
        ]
        if not eligible:
            raise PurposeError("REGIME_UNKNOWN")
        priority = min(r["priority"] for r in eligible)
        best = [r for r in eligible if r["priority"] == priority]
        if len(best) != 1:
            raise PurposeError("REGIME_PRIORITY_CONFLICT")
        selected = best[0]
        if selected.get("known") is None:
            raise PurposeError("REGIME_KNOWLEDGE_UNKNOWN")
        result["selected_regime_id"] = selected["id"]
        decision = resolve_price_limit_policy(
            board=row["board"],
            market_date=row["market_date"].isoformat(),
            status_known=True,
            is_st_class=status == "ST",
            listing_window_rule=ipo[0]["rule"],
            eligible_session_index=ordinal,
            ipo_window_sessions=int(ipo[0]["duration"]),
            observed_tradestatus="1" if row["is_tradable"] else "0",
            prior_trading_close=prior,
            policy=policy,
        )
        upper = None if decision.upper_limit_pct is None else decision.upper_limit_pct / 100
        lower = None if decision.lower_limit_pct is None else decision.lower_limit_pct / 100
        if (upper, lower) != (selected["upper_ratio"], selected["lower_ratio"]):
            raise PurposeError("NATIVE_REGIME_POLICY_DISAGREEMENT")
        if row["is_tradable"] and (row["upper_price"], row["lower_price"]) != (
            decision.upper_limit_price,
            decision.lower_limit_price,
        ):
            raise PurposeError("NATIVE_PRICE_BOUND_DISAGREEMENT")
        result.update(
            execution_source_qualified=True,
            qualified_entry_allowed=decision.entry_policy_allows
            and row["can_buy"] is True
            and row["is_tradable"] is True,
            reason_code=decision.reason_code,
        )
    except (PurposeError, ValueError) as exc:
        result["reason_code"] = str(exc)
    return result


def _schemas() -> tuple[Any, Any]:
    pa = importlib.import_module("pyarrow")
    instant, value = pa.timestamp("us", tz="UTC"), pa.decimal128(38, 10)
    state = pa.schema(
        [
            ("listing_id", pa.string()),
            ("security_id", pa.string()),
            ("market_date", pa.date32()),
            ("status", pa.string()),
            ("status_basis", pa.string()),
            ("ipo_rule", pa.string()),
            ("in_ipo_window", pa.bool_()),
            ("selected_regime_id", pa.string()),
            ("source_is_tradable", pa.bool_()),
            ("source_can_buy", pa.bool_()),
            ("source_can_sell", pa.bool_()),
            ("source_upper_price", value),
            ("source_lower_price", value),
            ("warmup_eligible", pa.bool_()),
            ("execution_source_qualified", pa.bool_()),
            ("qualified_entry_allowed", pa.bool_()),
            ("reason_code", pa.string()),
            ("evidence_digest", pa.string()),
            ("execution_authorized", pa.bool_()),
        ]
    )
    action = pa.schema(
        [
            ("action_id", pa.string()),
            ("security_id", pa.string()),
            ("effective_at", instant),
            ("ex_date", pa.date32()),
            ("action_status", pa.string()),
            ("native_knowledge_from", instant),
            ("qualified_knowledge_from", instant),
            ("cash_per_share", value),
            ("share_ratio", pa.decimal128(38, 18)),
            ("cash_unit", pa.string()),
            ("factor_ratio_observed", pa.decimal128(38, 18)),
            ("factor_ratio_expected", pa.decimal128(38, 18)),
            ("factor_ratio_error", pa.decimal128(38, 18)),
            ("factor_cross_check_status", pa.string()),
            ("qualified", pa.bool_()),
            ("reason_code", pa.string()),
            ("evidence_digest", pa.string()),
            ("execution_authorized", pa.bool_()),
        ]
    )
    return state, action


def _produce(root: Path, out: Path, scratch: Path) -> dict[str, Any]:
    pa, pq = importlib.import_module("pyarrow"), importlib.import_module("pyarrow.parquet")
    original = root / "source"
    manifest = source._read(original / "manifest.json")
    policy = source._read(root / "contract/price-limit.v2.json")
    action_policy = source._read(root / "contract/p1-corporate-action-normalization.v3.json")
    state_schema, action_schema = _schemas()
    c = source._connection(scratch)
    try:
        _latest(c, original, manifest["knowledge_cutoff"])
        _dated_status(c, root, manifest["knowledge_cutoff"])
        c.execute(
            "CREATE VIEW raw_ipo AS SELECT * FROM read_parquet("
            + source._q(root / "evidence/ipo-rules.parquet")
            + ")"
        )
        if c.execute("SELECT count(*)-count(DISTINCT interval_id) FROM raw_ipo").fetchone()[0]:
            raise PurposeError("IPO_RAW_DUPLICATE_KEY")
        raw_sha = file_sha(root / "evidence/ipo-rules.parquet")
        actions = _action_rows(c, action_policy)
        logging.info("native action qualification rows=%d", len(actions))
        action_dates = {
            (a["security_id"], a["ex_date"]) for a in actions if a["action_status"] == "IMPLEMENTED"
        }
        action_path = out / "actions.parquet"
        pq.write_table(pa.Table.from_pylist(actions, schema=action_schema), action_path)
        calendars = _rows(
            c,
            """SELECT DISTINCT v.mic_code,c.trade_date
            FROM p_dwd_trading_calendar c JOIN p_dwd_trading_venue v USING(venue_id)
            WHERE c.is_open AND c.visible_versions=1 AND v.visible_versions=1
            ORDER BY c.trade_date""",
        )
        dates: dict[str, list[date]] = defaultdict(list)
        for row in calendars:
            dates[{"XSHG": "SSE", "XSHE": "SZSE"}[row["mic_code"]]].append(row["trade_date"])
        dates = {k: sorted(set(v)) for k, v in dates.items()}
        prior: dict[str, Decimal | None] = {}
        ordinals: dict[str, int] = defaultdict(int)
        complete: dict[str, bool] = {}
        last_index: dict[str, int] = {}
        counts: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        per_security: Counter[str] = Counter()
        timings = []
        import time

        cursor, through = date(2026, 1, 1), date.fromisoformat(manifest["business_end"])
        while cursor <= through:
            start = time.monotonic()
            logging.info("qualification partition=%s start", cursor.strftime("%Y-%m"))
            following = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
            folder = out / cursor.strftime("%Y-%m")
            query = _market_query(original, cursor, following)
            c.execute(
                "EXPLAIN " + query
            ).fetchall()  # expose planning defects before writing this batch
            folder.mkdir()
            reader = c.execute(query).to_arrow_reader(batch_size=4096)
            rows_written = 0
            with pq.ParquetWriter(
                folder / "execution.parquet", state_schema, compression="zstd"
            ) as writer:
                for batch in reader:
                    result = []
                    for row in batch.to_pylist():
                        ident, day = row["listing_id"], row["market_date"]
                        sessions = dates[row["exchange"]]
                        index = bisect_left(sessions, day)
                        providers = [s for s in row["dated_status"] or [] if s["known"] is not None]
                        observed = (
                            len(providers) == 1 and providers[0]["reason"] == "PROVIDER_TRADING"
                        )
                        ipo = [r for r in row["regimes"] or [] if r["ipo"] is not None]
                        if ident not in complete:
                            first = ipo[0]["start_date"] if len(ipo) == 1 else None
                            complete[ident] = bool(
                                first
                                and first >= date(2026, 1, 1)
                                and bisect_left(sessions, first) == index
                            )
                        elif last_index[ident] != index - 1:
                            complete[ident] = False
                        if not providers:
                            complete[ident] = False
                        last_index[ident] = index
                        ordinals[ident] += int(observed)
                        qualified = _state_row(
                            row,
                            ordinal=ordinals[ident],
                            complete=complete[ident],
                            prior=prior.get(ident),
                            policy=policy,
                            raw_sha=raw_sha,
                        )
                        if (row["security_id"], day) in action_dates:
                            # An event-day reference is not the prior unadjusted close.
                            # Preserve native bounds; defer affected execution until
                            # an exact reference-price attestation exists.
                            qualified.update(
                                execution_source_qualified=False,
                                qualified_entry_allowed=False,
                                reason_code="ACTION_REFERENCE_REQUIRED",
                            )
                        for key in (
                            "warmup_eligible",
                            "execution_source_qualified",
                            "qualified_entry_allowed",
                        ):
                            counts[key] += int(qualified[key])
                        per_security[ident] += int(qualified["warmup_eligible"])
                        reasons[qualified["reason_code"]] += 1
                        if observed:
                            prior[ident] = (
                                row["close"] if row["close"] and row["close"] > 0 else None
                            )
                        elif len(providers) != 1:
                            prior[ident] = None
                        result.append(qualified)
                    writer.write_table(pa.Table.from_pylist(result, schema=state_schema))
                    rows_written += len(result)
            timings.append(
                dict(
                    partition=cursor.strftime("%Y-%m"),
                    rows=rows_written,
                    seconds=round(time.monotonic() - start, 3),
                    disposition="BUILD_ONCE",
                )
            )
            counts["market_rows"] += rows_written
            logging.info(
                "qualification partition=%s rows=%d seconds=%.3f",
                cursor.strftime("%Y-%m"),
                rows_written,
                time.monotonic() - start,
            )
            cursor = following
        return dict(
            schema_version=VERSION,
            market=dict(counts),
            reasons=dict(sorted(reasons.items())),
            actions=dict(Counter(a["reason_code"] for a in actions)),
            action_rows=len(actions),
            action_qualified=sum(a["qualified"] for a in actions),
            securities_with_120_warmup_rows=sum(v >= 120 for v in per_security.values()),
            historical_pit_granted=False,
            paper_entry_granted=False,
            official_execution_authorized=False,
            stage_metrics=timings,
        )
    finally:
        c.close()


def _policies(root: Path) -> None:
    for name, expected in POLICY_HASHES.items():
        path = root / "contract" / name
        if file_sha(path) != expected:
            raise PurposeError("FROZEN_POLICY_VERSION")
    if source._read(root / "contract/p6-base24-purpose.v1.json") != CONTRACT:
        raise PurposeError("PURPOSE_POLICY")


def build(
    handoff: Path,
    output: Path,
    *,
    manifest_sha256: str,
    ipo_source: Path,
    ipo_source_sha256: str,
    config_root: Path,
    code_revision: str,
    status_sources: tuple[tuple[Path, str], ...] = (),
    status_inventory: tuple[Path, str] | None = None,
) -> dict[str, Any]:
    """Build a self-contained qualification beside, never over, sealed inputs."""
    handoff, ipo_source, config_root = map(physical_path, (handoff, ipo_source, config_root))
    status_paths = [(physical_path(p), sha) for p, sha in status_sources]
    inventory_path = physical_path(status_inventory[0]) if status_inventory else None
    roots = [handoff, ipo_source.parent, config_root, *(p.parent for p, _ in status_paths)]
    if inventory_path:
        roots.append(inventory_path.parent)
    output = disjoint_output(output, roots)
    if output.exists():
        raise PurposeError("OUTPUT_EXISTS")
    if file_sha(ipo_source) != ipo_source_sha256:
        raise PurposeError("IPO_SOURCE_HASH")
    for name, expected in POLICY_HASHES.items():
        if file_sha(config_root / name) != expected:
            raise PurposeError("FROZEN_POLICY_VERSION")
    contract_path = config_root / "forward-validation/data/p6-base24-purpose.v1.json"
    if source._read(contract_path) != CONTRACT:
        raise PurposeError("PURPOSE_POLICY")
    selected_status: dict[str, Any] = {}
    if status_paths:
        if status_inventory is None or inventory_path is None:
            raise PurposeError("STATUS_INVENTORY_REQUIRED")
        if file_sha(inventory_path) != status_inventory[1]:
            raise PurposeError("STATUS_INVENTORY_HASH")
        inventory = source._read(inventory_path)
        members = inventory["content"]["files"]
        for p, sha in status_paths:
            if file_sha(p) != sha:
                raise PurposeError("STATUS_SOURCE_HASH")
            matches = [
                m
                for m in members
                if m["sha256"].removeprefix("sha256:") == sha.removeprefix("sha256:")
                and m["size_bytes"] == p.stat().st_size
                and m["path"].endswith("/daily_status_observation_delta.parquet")
            ]
            if not matches:
                raise PurposeError("STATUS_SOURCE_MEMBERSHIP")
            selected_status[sha] = dict(
                original_members=matches, inventory_sha256=status_inventory[1]
            )
    paths = [handoff / rel for rel in _tree(handoff)] + [
        ipo_source,
        config_root / "price-limit.v2.json",
        config_root / "p1-corporate-action-normalization.v3.json",
        contract_path,
        *(p for p, _ in status_paths),
        *([inventory_path] if inventory_path else []),
    ]
    before = {p: _fingerprint(p) for p in paths}
    original_set = _tree(handoff)
    source.verify(handoff, manifest_sha256)
    output.mkdir()
    shutil.copytree(handoff, output / "source", copy_function=shutil.copyfile)
    (output / "evidence").mkdir()
    (output / "contract").mkdir()
    shutil.copyfile(ipo_source, output / "evidence/ipo-rules.parquet")
    if file_sha(output / "evidence/ipo-rules.parquet") != ipo_source_sha256:
        raise PurposeError("IPO_SOURCE_CHANGED")
    for p in (
        config_root / "price-limit.v2.json",
        config_root / "p1-corporate-action-normalization.v3.json",
        contract_path,
    ):
        shutil.copyfile(p, output / "contract" / p.name)
    (output / "evidence/status").mkdir()
    for p, sha in status_paths:
        dest = output / "evidence/status" / (sha[7:] + ".parquet")
        if not dest.exists():
            shutil.copyfile(p, dest)
    if inventory_path:
        shutil.copyfile(inventory_path, output / "evidence/status-original-manifest.json")
    source._write(output / "evidence/status-selection.json", selected_status)
    _policies(output)
    products = output / "qualified"
    products.mkdir()
    with TemporaryDirectory(prefix="p6-purpose-build-", dir=output.parent) as directory:
        result = _produce(output, products, Path(directory))
    if _tree(handoff) != original_set or any(_fingerprint(p) != fp for p, fp in before.items()):
        raise PurposeError("SOURCE_CHANGED")
    source._write(output / "assessment.json", result)
    members = {
        rel: {"sha256": file_sha(output / rel), "bytes": (output / rel).stat().st_size}
        for rel in sorted(_tree(output))
    }
    manifest = dict(
        schema_version=VERSION,
        code_revision=code_revision,
        source_manifest_sha256=manifest_sha256,
        ipo_source_sha256=ipo_source_sha256,
        built_at=datetime.now(timezone.utc).isoformat(),
        consumer_admission="PURPOSE_EVIDENCE_NOT_RUNTIME_ADMISSION",
        execution_authorized=False,
        files=members,
    )
    source._write(output / "manifest.json", manifest)
    (output / "READY").write_text(file_sha(output / "manifest.json") + "\n")
    return result


def _scratch_parent(root: Path, supplied: Path | None = None) -> Path:
    # Inspect the configured path before tempfile can probe it with a test write.
    parent = physical_path(supplied or Path(os.environ.get("TMPDIR", "/tmp")))
    if parent == root or root in parent.parents:
        raise PurposeError("SCRATCH_SOURCE_OVERLAP")
    return parent


def verify(root: Path, manifest_sha256: str, *, scratch_root: Path | None = None) -> dict[str, Any]:
    """Recompute the complete qualification using only local physical evidence."""
    root = physical_path(root)
    scratch_parent = _scratch_parent(root, scratch_root)
    original_set = _tree(root)
    before = {rel: _fingerprint(root / rel) for rel in original_set}
    if file_sha(root / "manifest.json") != manifest_sha256:
        raise PurposeError("MANIFEST_HASH")
    manifest = source._read(root / "manifest.json")
    if (
        manifest.get("schema_version") != VERSION
        or manifest.get("consumer_admission") != "PURPOSE_EVIDENCE_NOT_RUNTIME_ADMISSION"
        or manifest.get("execution_authorized") is not False
    ):
        raise PurposeError("PURPOSE_CONTRACT")
    if (root / "READY").read_text().strip() != manifest_sha256:
        raise PurposeError("READY_IDENTITY")
    if set(_tree(root)) != {*manifest["files"], "manifest.json", "READY"}:
        raise PurposeError("PHYSICAL_SET")
    for rel, member in manifest["files"].items():
        p = source._member(root, rel)
        if p.stat().st_size != member["bytes"] or file_sha(p) != member["sha256"]:
            raise PurposeError("MEMBER_HASH:" + rel)
    if file_sha(root / "evidence/ipo-rules.parquet") != manifest["ipo_source_sha256"]:
        raise PurposeError("IPO_SOURCE_HASH")
    _policies(root)
    source.verify(root / "source", manifest["source_manifest_sha256"], scratch_root=scratch_parent)
    with TemporaryDirectory(prefix="p6-purpose-cold-", dir=scratch_parent) as directory:
        scratch = Path(directory)
        expected = scratch / "expected"
        expected.mkdir()
        result = _produce(root, expected, scratch)
        # Timing is operational evidence, not semantics. Values/schemas, full
        # output file set and all key-bearing rows are compared independently.
        recorded = source._read(root / "assessment.json")
        for obj in (recorded, result):
            obj.pop("stage_metrics", None)
        if recorded != result:
            raise PurposeError("ASSESSMENT_SEMANTICS")
        if set(_tree(expected)) != set(_tree(root / "qualified")):
            raise PurposeError("QUALIFIED_PHYSICAL_SET")
        c = source._connection(scratch)
        try:
            for rel in sorted(_tree(expected)):
                left = f"SELECT * FROM read_parquet({source._q(expected / rel)})"
                right = f"SELECT * FROM read_parquet({source._q(root / 'qualified' / rel)})"
                if (
                    c.execute(left + " LIMIT 0").description
                    != c.execute(right + " LIMIT 0").description
                ):
                    raise PurposeError("QUALIFIED_SCHEMA:" + rel)
                if c.execute(
                    f"SELECT count(*) FROM ((({left}) EXCEPT ALL ({right})) "
                    f"UNION ALL (({right}) EXCEPT ALL ({left})))"
                ).fetchone()[0]:
                    raise PurposeError("QUALIFIED_VALUES:" + rel)
        finally:
            c.close()
    if _tree(root) != original_set or any(
        _fingerprint(root / rel) != fingerprint for rel, fingerprint in before.items()
    ):
        raise PurposeError("QUALIFICATION_CHANGED")
    return result
