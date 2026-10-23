"""P10 dated execution qualification using the frozen Base 2.4 policy.

The pure evaluator is retained from base24_p6_purpose at c1faad291cce16cc153de1780ba5a5a58bbaae61.
This module never executes P6 or mutates its data. New observations have a true
qualification clock and a separate support identity, not rewritten source times.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.price_limits import resolve_price_limit_policy

from .p10_action_support import _digest
from .p10_session_schedule import RULES


class PurposeError(ValueError):
    """An affected execution observation remains unavailable."""


_IPO_RULES = {
    "ipo_first_5_trading_days_no_limit",
    "ipo_main_first_trading_day_44pct",
    "ipo_main_first_day_legacy_special",
    "ipo_legacy_main_first_day_special_not_modeled_full",
}


def _validate_current_window(proof: dict[str, Any], row: dict[str, Any]) -> None:
    """The counted distinct dated trades are supplied by the verified SQL closure.

    A nonzero listing age or open-calendar count cannot substitute for this count.
    No initial/relisting session is reconstructed. Rule bodies are verified once
    per job against the pinned authority before this pure row function is called.
    """
    mic = "XSHG" if row["board"] == "SSE_main" else "XSHE"
    if (
        row["board"] not in {"SSE_main", "SZ_main", "ChiNext"}
        or row["market_date"] < date(2026, 7, 6)
        or proof.get("rule_sha256") != RULES[mic]["sha256"]
        or proof.get("listing_id") != row["listing_id"]
        or proof.get("market_date") != row["market_date"]
        or type(proof.get("observed_trading_sessions")) is not int
        or proof["observed_trading_sessions"] < 6
        or proof.get("rule_known_at") is None
        or proof["rule_known_at"] > row["qualification_cutoff"]
        or not any(
            x["kind"] == "LISTING_LIFECYCLE" and x["value"] == "LISTED" for x in row["lifecycle"]
        )
        or any(
            x["value"] in {"DELISTING", "DELISTED", "RELISTED", "SUSPENDED"}
            for x in row["lifecycle"]
        )
    ):
        raise PurposeError("CURRENT_RULE_WINDOW_UNQUALIFIED")


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


def _state_row(
    row: dict[str, Any],
    *,
    ordinal: int,
    complete: bool,
    prior: Decimal | None,
    policy: dict[str, Any],
    raw_sha: str,
    window_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Qualify original state; never change source flags, bounds, or missing bars."""
    statuses, lifecycle, regimes = (
        row.get("statuses") or [],
        row.get("lifecycle") or [],
        row.get("regimes") or [],
    )
    dated = row.get("dated_status") or []
    # These native joins return unordered collections. Their traversal order is
    # not source evidence; preserve every typed value and duplicate, while
    # making the evidence identity independent of the database hash-join order.
    evidence = dict(row)
    if window_proof is not None:
        evidence["successor_window_proof"] = window_proof
    for name in ("statuses", "lifecycle", "regimes", "dated_status"):
        if isinstance(row.get(name), list):
            evidence[name] = sorted(row[name], key=_digest)
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
        qualified_upper_price=None,
        qualified_lower_price=None,
        bound_basis=None,
        warmup_eligible=False,
        execution_source_qualified=False,
        qualified_entry_allowed=False,
        reason_code="MARKET_OBSERVATION_MISSING",
        evidence_digest=_digest(evidence),
        execution_authorized=False,
    )
    try:
        cutoff = row.get("qualification_cutoff")
        known = [row.get("bar_known"), row.get("state_known")]
        known.extend(
            x.get("known") for group in (statuses, lifecycle, regimes, dated) for x in group
        )
        if (
            not isinstance(cutoff, datetime)
            or cutoff.utcoffset() is None
            or any(
                not isinstance(t, datetime) or t.utcoffset() is None or t > cutoff for t in known
            )
        ):
            raise PurposeError("CONTEXT_KNOWLEDGE_AFTER_CUTOFF")
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
        if window_proof is not None:
            if ipo:
                raise PurposeError("SUCCESSOR_CANNOT_REPLACE_EXISTING_IPO_RULE")
            try:
                _validate_current_window(window_proof, row)
            except PurposeError as exc:
                raise PurposeError(str(exc)) from exc
            initial, window_rule, window_duration = False, None, 0
            result.update(
                ipo_rule=(
                    "CURRENT_SSE_RULE_WINDOW_EXCLUDED"
                    if row["board"] == "SSE_main"
                    else "CURRENT_SZSE_RULE_WINDOW_EXCLUDED"
                ),
                in_ipo_window=False,
            )
        else:
            if len(ipo) != 1 or ipo[0]["raw_evidence"] != raw_sha or ipo[0]["versions"] != 1:
                raise PurposeError("IPO_SOURCE_CLOSURE")
            initial = ipo_window(ipo[0]["rule"], ipo[0]["duration"], ordinal, complete)
            window_rule, window_duration = ipo[0]["rule"], int(ipo[0]["duration"])
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
            listing_window_rule=window_rule,
            eligible_session_index=ordinal,
            ipo_window_sessions=window_duration,
            observed_tradestatus="1" if row["is_tradable"] else "0",
            prior_trading_close=prior,
            policy=policy,
        )
        upper = None if decision.upper_limit_pct is None else decision.upper_limit_pct / 100
        lower = None if decision.lower_limit_pct is None else decision.lower_limit_pct / 100
        if (upper, lower) != (selected["upper_ratio"], selected["lower_ratio"]):
            raise PurposeError("NATIVE_REGIME_POLICY_DISAGREEMENT")
        native_bounds = (row["upper_price"], row["lower_price"])
        expected_bounds = (decision.upper_limit_price, decision.lower_limit_price)
        if row["is_tradable"]:
            if native_bounds == (None, None):
                result["bound_basis"] = "FROZEN_POLICY_DERIVED_NATIVE_NULLS_PRESERVED"
            elif native_bounds == expected_bounds:
                result["bound_basis"] = "NATIVE_AND_FROZEN_POLICY_AGREE"
            else:
                raise PurposeError("NATIVE_PRICE_BOUND_DISAGREEMENT")
            if selected["kind"] != "NO_LIMIT" and None in expected_bounds:
                raise PurposeError("REFERENCE_PRICE_UNAVAILABLE")
            result.update(
                qualified_upper_price=expected_bounds[0], qualified_lower_price=expected_bounds[1]
            )
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


def context_query(start: date, end: date) -> str:
    """One monthly context from verified relations; no historical source lookups.

    Relation contracts are established by the immutable job's input verifier.
    Every keyed source version and interval remains in the returned evidence.
    """
    if not (type(start) is date and type(end) is date and start < end):
        raise PurposeError("CONTEXT_MONTH")
    return f"""WITH month AS (
        SELECT * FROM joined WHERE trade_date>=DATE '{start}' AND trade_date<DATE '{end}'
    ) SELECT j.listing_id,j.security_id,j.listing_code,j.trade_date AS market_date,
        j.open,j.high,j.low,j.close,j.volume,j.turnover AS amount,
        j.is_tradable,j.can_buy,j.can_sell,
        j.bar_knowledge_from AS bar_known,j.state_knowledge_from AS state_known,
        l.board_code AS board,
        1 AS listing_count,1 AS security_count,1 AS bar_count,1 AS calendar_count,1 AS venue_count,
        CASE WHEN t.listing_id IS NOT NULL THEN 1 ELSE 0 END AS state_versions,
        t.upper_price,t.lower_price,
        (SELECT list(struct_pack(value:=s.status_value,reason:=s.reason,known:=s.acquired_at,
            evidence:=s.source_refs)) FROM effective_status s WHERE s.stock_code=j.listing_code
            AND s.market_date=j.trade_date) AS dated_status,
        (SELECT list(struct_pack(kind:=s.status_type,value:=s.status_value,versions:=1,
            known:=s.knowledge_from,evidence:=s.source_artifact_sha256))
            FROM security_status s WHERE s.security_id=j.security_id
            AND s.effective_from<=j.observation_start
            AND coalesce(s.effective_to,'infinity'::TIMESTAMPTZ)>j.observation_start) AS statuses,
        (SELECT list(struct_pack(kind:=s.status_type,value:=s.status_value,versions:=1,
            known:=s.knowledge_from)) FROM listing_status s WHERE s.listing_id=j.listing_id
            AND s.effective_from<=j.observation_start
            AND coalesce(s.effective_to,'infinity'::TIMESTAMPTZ)>j.observation_start) AS lifecycle,
        (SELECT list(struct_pack(id:=r.price_limit_regime_id,priority:=r.priority,
            kind:=r.regime_type,status:=r.security_status,ipo:=r.ipo_phase,
            upper_ratio:=r.upper_limit_ratio,lower_ratio:=r.lower_limit_ratio,
            rule:=i.listing_window_rule,duration:=i.no_limit_days,
            start_date:=i.effective_start,versions:=1,raw_evidence:=r.source_artifact_sha256,
            known:=r.knowledge_from)) FROM regimes r LEFT JOIN raw_ipo i
            ON r.price_limit_regime_id='price_limit_regime_'||substr(sha256(i.interval_id),1,32)
            WHERE r.venue_id=j.venue_id AND (r.listing_id IS NULL OR r.listing_id=j.listing_id)
            AND (r.board_code IS NULL OR r.board_code=l.board_code)
            AND r.effective_from<=j.observation_start
            AND coalesce(r.effective_to,'infinity'::TIMESTAMPTZ)>j.observation_start) AS regimes,
        h.trading_count,h.prior_close,h.prior_status_known,
        j.mic_code,j.calendar_session_id,
        calendar_rank.ordinal-listing_floor.ordinal AS listing_sessions_lower_bound
    FROM month j JOIN listings l ON l.listing_id=j.listing_id
        AND l.effective_from<=j.observation_start
        AND coalesce(l.effective_to,'infinity'::TIMESTAMPTZ)>j.observation_start
    LEFT JOIN native_states t ON t.listing_id=j.listing_id
        AND t.calendar_session_id=j.calendar_session_id
    JOIN observed_history h ON h.listing_id=j.listing_id
        AND h.calendar_session_id=j.calendar_session_id
    JOIN calendar_rank ON calendar_rank.venue_id=j.venue_id
        AND calendar_rank.trade_date=j.trade_date
    JOIN listing_floor ON listing_floor.listing_id=j.listing_id
    ORDER BY j.listing_id,j.trade_date"""


def prepare_calendar_counts(connection: Any, snapshot: datetime) -> dict[str, int]:
    """Count the exact visible calendar versions, never archived revisions twice."""
    if not isinstance(snapshot, datetime) or snapshot.utcoffset() is None:
        raise PurposeError("CALENDAR_SNAPSHOT_REQUIRED")
    connection.execute(
        """CREATE TEMP TABLE qualified_calendar AS SELECT * FROM full_calendar
        WHERE knowledge_from<=? AND coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>?""",
        [snapshot, snapshot],
    )
    for keys in ("calendar_session_id", "venue_id,trade_date"):
        duplicates = connection.execute(
            f"SELECT count(*) FROM (SELECT {keys} FROM qualified_calendar "
            "GROUP BY ALL HAVING count(*)<>1)"
        ).fetchone()[0]
        if duplicates:
            raise PurposeError("CALENDAR_VISIBLE_VERSION_AMBIGUOUS")
    connection.execute("""CREATE TEMP TABLE calendar_rank AS
        SELECT venue_id,trade_date,count(*) OVER (PARTITION BY venue_id ORDER BY trade_date)
        AS ordinal FROM qualified_calendar WHERE is_open""")
    connection.execute("""CREATE TEMP TABLE listing_floor AS
        SELECT l.listing_id,count(c.trade_date) AS ordinal FROM listings l
        LEFT JOIN qualified_calendar c ON c.venue_id=l.venue_id AND c.is_open
        AND c.trade_date<CAST(l.effective_from AS DATE) GROUP BY l.listing_id""")
    return {
        "raw_rows": connection.execute("SELECT count(*) FROM full_calendar").fetchone()[0],
        "visible_rows": connection.execute("SELECT count(*) FROM qualified_calendar").fetchone()[0],
    }
