"""Route whole daily observations without changing admission or operation budgets."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_delivery_common import atomic
from quanthus.providers import daily_source_failover as routing
from quanthus.providers import tencent_daily
from quanthus.providers.security_universe import verify


def acquire(
    *,
    paths: Any,
    market_date: date,
    codes: list[str],
    landing: Path,
    readiness: Any,
    readiness_deadline: datetime,
    completion_deadline: datetime,
    captured: dict[str, Any],
    universe: dict[str, Any],
    converter_sha256: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from quanthus.pipelines import lane_runner as lane
    from quanthus.providers.market_raw_reuse import load

    cfg = routing.policy()
    circuit = paths.home / "source-failover-v1"
    primary_log = []
    index_rows: list[dict[str, Any]] = []
    bars: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    primary_error = None
    remaining = (completion_deadline - datetime.now(lane.SH)).total_seconds()
    if remaining <= 0:
        raise routing.BudgetExhausted("DAILY_SHARED_WALL_BUDGET_EXHAUSTED")
    primary_deadline = min(
        completion_deadline,
        datetime.now(lane.SH)
        + timedelta(
            seconds=min(
                cfg["primary_scan_seconds"], remaining * cfg["primary_max_remaining_fraction"]
            )
        ),
    )

    def primary() -> None:
        nonlocal index_rows, bars, states, primary_log
        limit = min(
            readiness_deadline,
            primary_deadline,
            datetime.now(lane.SH) + timedelta(seconds=cfg["primary_readiness_seconds"]),
        )
        index_rows, logs = lane._baostock_wait_for_index_day(
            market_date,
            deadline_at=limit,
            poll_interval_seconds=float(readiness["poll_interval_seconds"]),
            socket_timeout_seconds=float(readiness["socket_timeout_seconds"]),
            progress_path=landing / "provider_readiness.json",
            captured_responses=captured,
        )
        primary_log.extend(logs)
        bars, states, logs = lane._baostock_fetch_day(
            codes,
            market_date,
            deadline_at=primary_deadline,
            missing_set_retry_rounds=int(readiness["missing_set_retry_rounds"]),
            missing_set_retry_delay_seconds=float(readiness["missing_set_retry_delay_seconds"]),
            socket_timeout_seconds=float(readiness["socket_timeout_seconds"]),
            captured_responses=captured,
        )
        primary_log.extend(logs)

    try:
        routing.invoke("baostock", "daily", circuit, primary)
    except routing.SourceUnavailable as exc:
        primary_error = str(exc)
        # Retain useful responses obtained before a source failure in this Job.
        captured = load(
            shadow=paths.shadow_root,
            day=market_date,
            landing=landing,
            stock_codes={lane._to_bs_code(c) for c in codes},
            index_codes=set(lane.INDEX_CODES.values()),
            universe_digest=universe["universe_digest"],
            converter_sha256=converter_sha256,
            include_current=True,
        )
        ready_codes = [c for c in codes if lane._to_bs_code(c) in captured]
        if ready_codes:
            bars, states, logs = lane._baostock_fetch_day(
                ready_codes,
                market_date,
                missing_set_retry_rounds=0,
                deadline_at=completion_deadline,
                captured_responses=captured,
            )
            primary_log.extend(logs)
        if all(code in captured for code in lane.INDEX_CODES.values()):
            index_rows, logs = lane._baostock_fetch_index_day(
                market_date, captured_responses=captured
            )
            primary_log.extend(logs)
    if (
        len(index_rows) != len(lane.INDEX_CODES)
        or {row["index_code"] for row in index_rows} != set(lane.INDEX_CODES)
        or any(row.get("date") != str(market_date) for row in index_rows)
    ):
        raise routing.SourceUnavailable("REQUIRED_TARGET_INDEX_EVIDENCE_MISSING")
    # A fresh unrelated listing denominator is never invented from yesterday.
    root = paths.home / "source-universe-v1" / str(market_date)
    checked = verify(root, market_date)
    if checked != universe:
        raise ValueError("DAILY_FALLBACK_UNIVERSE_IDENTITY_DIFFERS")
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    population = {
        row["stock_code"]: row for row in pq.read_table(root / "stock_list.parquet").to_pylist()
    }
    by_code = {row["stock_code"]: row for row in bars}
    status_by = {row["stock_code"]: row for row in states}
    failures = []
    saved_tencent = tencent_daily.reuse(
        paths.shadow_root, landing, market_date, set(codes) - set(by_code)
    )
    for code in codes:
        if code in by_code or (code in status_by and status_by[code]["tradestatus"] == "0"):
            continue
        if datetime.now(lane.SH) >= completion_deadline:
            failures.append({"code": code, "reason": "shared_wall_budget_exhausted"})
            break
        authority = population[code]
        if authority["tradestatus"] == "0":
            # Explicit target-date suspension from the independently sealed universe.
            status_by[code] = {
                "security_id": code,
                "stock_code": code,
                "date": str(market_date),
                "market_date": str(market_date),
                "tradestatus": "0",
                "isST": None,
                "source": "baostock.target-date-universe;isST=unavailable",
                "acquired_at": authority["warehouse_observed_at"],
                "built_at": authority["warehouse_observed_at"],
            }
            continue
        if authority["tradestatus"] != "1":
            raise ValueError("DAILY_FALLBACK_TARGET_STATUS_UNKNOWN")
        try:
            if code in saved_tencent:
                bar, receipt = saved_tencent[code]
            else:
                bar, receipt = routing.invoke(
                    "tencent",
                    "daily",
                    circuit,
                    partial(
                        tencent_daily.fetch,
                        code,
                        market_date,
                        root=landing / "original-responses",
                        deadline=completion_deadline,
                    ),
                )
        except (routing.SourceUnavailable, routing.BudgetExhausted) as exc:
            failures.append({"code": code, "reason": str(exc)})
            break  # Provider unavailable; do not issue thousands of identical failed probes.
        except ValueError as exc:
            if not str(exc).startswith("TENCENT_"):
                raise
            failures.append({"code": code, "reason": str(exc)})
            continue
        by_code[code] = bar
        status_by[code] = {
            "security_id": code,
            "stock_code": code,
            "date": str(market_date),
            "market_date": str(market_date),
            "tradestatus": "1",
            "isST": None,
            "source": "baostock.target-date-universe;isST=unavailable",
            "acquired_at": authority["warehouse_observed_at"],
            "built_at": authority["warehouse_observed_at"],
        }
        primary_log.append(receipt)
    record = {
        "policy_id": routing.POLICY,
        "market_date": str(market_date),
        "primary_error": primary_error,
        "universe_digest": universe["universe_digest"],
        "fallback_unit": "whole_security_daily_bar",
        "source_counts": {
            source: sum(row["source"] == source for row in by_code.values())
            for source in sorted({r["source"] for r in by_code.values()})
        },
        "st_unknown": sum(r.get("isST") not in {"0", "1"} for r in status_by.values()),
        "failures": failures,
        "required_index_units": (
            "Tencent indices not qualified; verified BaoStock target-date observations required"
        ),
        "execution_authorized": False,
        "st_product_admission_unchanged": True,
    }
    atomic(landing / "daily-source-failover.json", record)
    primary_log.append(
        {"provider": "daily-source-router", "acquired_at": lane._now_sh_iso(), "decision": record}
    )
    return (
        [by_code[c] for c in codes if c in by_code],
        [status_by[c] for c in codes if c in status_by],
        index_rows,
        primary_log,
    )
