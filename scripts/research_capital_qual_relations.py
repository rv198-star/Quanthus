#!/usr/bin/env python3
"""One independently written key/state/action check over the verified A inputs."""

import argparse
import collections
import json
import resource
import subprocess
import time
from pathlib import Path

from quanthus.backtest.native_capital_adapter import NativeAdapterError, qualify_action
from quanthus.hf_dwd_research import digest, read, verify_members, write

ROOT = Path(__file__).resolve().parents[1]


def main():
    import duckdb

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-job", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--expected-git", required=True)
    a = p.parse_args()
    began = time.monotonic()
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        == a.expected_git
    )
    spec = read(a.request)
    q = read(ROOT / spec["parent_request"])
    assert digest(q) == spec["parent_request_digest"]
    previous = read(a.source_job / "census.json")
    assert previous["digest"] == spec["parent_census_digest"]
    assert previous["digest"] == digest({k: v for k, v in previous.items() if k != "digest"})
    assert previous["identity"]["request_digest"] == digest(q)
    members = read(ROOT / q["closure"]["path"])["members"]
    verify_members(a.source_job / "source", q, members)
    assert not a.out.exists(), "one independent relation check only"
    a.out.mkdir(parents=True)
    resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    db.execute("SET memory_limit='2GB'")
    db.execute("SET threads=2")

    def load(name, predicate):
        paths = [
            str(a.source_job / "source/DWD" / v["path"])
            for v in members
            if v["path"].startswith("payload/" + name + "/")
        ]
        db.read_parquet(paths, hive_partitioning=True).create_view("raw")
        db.execute(
            f"CREATE TEMP TABLE v_{name} AS SELECT * FROM raw "
            f"WHERE knowledge_to IS NULL AND {predicate}"
        )

    date_range = "BETWEEN DATE '2020-01-02' AND DATE '2021-12-31'"
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        load(table, "CAST(observation_start AS DATE) " + date_range)
    for table in ("dwd_security", "dwd_listing"):
        load(
            table,
            "CAST(effective_from AS DATE)<=DATE '2021-12-31' AND "
            "(effective_to IS NULL OR CAST(effective_to AS DATE)>DATE '2020-01-02')",
        )
    load(
        "dwd_trading_calendar",
        "CAST(trade_date AS DATE) BETWEEN DATE '2019-01-01' AND DATE '2021-12-31'",
    )
    for table in ("dwd_corporate_action", "dwd_corporate_action_term"):
        load(table, "CAST(effective_at AS DATE) " + date_range)
    load("dwd_quality_exception", "CAST(affected_effective_at AS DATE) " + date_range)

    def scalar(query):
        return db.execute(query).fetchone()[0]

    def rows(query):
        cursor = db.execute(query)
        names = [col[0] for col in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    joins = (
        "FROM v_dwd_tradability_state t LEFT JOIN v_dwd_security_bar b "
        "USING(listing_id,calendar_session_id)"
    )
    counts = {
        "state_rows": scalar("SELECT count(*) FROM v_dwd_tradability_state"),
        "bar_rows": scalar("SELECT count(*) FROM v_dwd_security_bar"),
        "state_without_bar": scalar("SELECT count(*) " + joins + " WHERE b.listing_id IS NULL"),
        "missing_observation_with_positive_bar": scalar(
            "SELECT count(*) "
            + joins
            + " WHERE t.reason_code='TRADABLE_OBSERVATION_MISSING' AND b.open>0 AND b.close>0"
        ),
        "bar_without_state": scalar(
            "SELECT count(*) FROM v_dwd_security_bar b WHERE NOT EXISTS "
            "(SELECT 1 FROM v_dwd_tradability_state t WHERE t.listing_id=b.listing_id "
            "AND t.calendar_session_id=b.calendar_session_id)"
        ),
        "state_without_listing": scalar(
            "SELECT count(*) FROM v_dwd_tradability_state t WHERE NOT EXISTS "
            "(SELECT 1 FROM v_dwd_listing l WHERE l.listing_id=t.listing_id "
            "AND l.effective_from<=t.observation_start "
            "AND (l.effective_to IS NULL OR l.effective_to>t.observation_start))"
        ),
        "state_without_calendar": scalar(
            "SELECT count(*) FROM v_dwd_tradability_state t WHERE NOT EXISTS "
            "(SELECT 1 FROM v_dwd_trading_calendar c "
            "WHERE c.calendar_session_id=t.calendar_session_id)"
        ),
        "calendar_open_missing_times": scalar(
            "SELECT count(*) FROM v_dwd_trading_calendar WHERE is_open "
            "AND (open_at IS NULL OR close_at IS NULL)"
        ),
        "observed_state_with_positive_bar_missing_bounds": scalar(
            "SELECT count(*) "
            + joins
            + " WHERE t.reason_code='TRADABLE_OBSERVED' AND b.open>0 AND b.close>0 "
            "AND (t.upper_price IS NULL OR t.lower_price IS NULL "
            "OR t.upper_price<=0 OR t.lower_price<=0)"
        ),
    }
    examples = rows(
        "SELECT t.listing_id,t.calendar_session_id,t.observation_start,t.reason_code "
        + joins
        + " WHERE t.reason_code='TRADABLE_OBSERVATION_MISSING' AND b.open>0 AND b.close>0 "
        "ORDER BY t.listing_id,t.calendar_session_id LIMIT 8"
    )
    headers = rows("SELECT * FROM v_dwd_corporate_action ORDER BY corporate_action_id")
    terms = collections.defaultdict(list)
    for term in rows("SELECT * FROM v_dwd_corporate_action_term"):
        terms[term["corporate_action_id"]].append(term)
    reasons = collections.Counter()
    bad = []
    events = collections.defaultdict(list)
    qualified = 0
    late = 0
    for header in headers:
        try:
            payload = qualify_action(header, terms[header["corporate_action_id"]])
        except NativeAdapterError as exc:
            reasons[str(exc)] += 1
            if header["action_status"] == "IMPLEMENTED":
                bad.append(
                    {
                        "action_id": header["corporate_action_id"],
                        "security_id": header["security_id"],
                        "effective_at": header["effective_at"],
                        "ex_date": header["ex_date"],
                        "reason": str(exc),
                        "term_types": [
                            t["term_type"] for t in terms[header["corporate_action_id"]]
                        ],
                        "source_artifact_sha256": header["source_artifact_sha256"],
                    }
                )
            continue
        qualified += 1
        late += not payload.source_clock.source_pit_available(header["effective_at"])
        events[(payload.security_id, payload.ex_date)].append(
            {
                "action_id": payload.action_id,
                "cash": str(payload.cash_per_share),
                "shares": str(payload.shares_per_share),
            }
        )
    duplicates = [
        {"security_id": key[0], "ex_date": key[1], "actions": value}
        for key, value in events.items()
        if len(value) > 1
    ]
    quality = rows(
        "SELECT quality_exception_id,affected_table,affected_business_key_json,rule_id,"
        "classification,resolution_state,disposition FROM v_dwd_quality_exception "
        "ORDER BY quality_exception_id"
    )
    from quanthus.backtest.native_capital_qualification import clean

    result = clean(
        {
            "schema": "quanthus.native-capital-independent-relations.v1",
            "execution_git": a.expected_git,
            "request_digest": digest(spec),
            "parent_census_digest": previous["digest"],
            "mode": (
                "independent key/state/unit qualification, verified physical source reused; "
                "not a capital cold replay"
            ),
            "counts": counts,
            "missing_observation_examples": examples,
            "action_economic_terms_qualified": qualified,
            "qualified_actions_with_source_knowledge_later_than_business": late,
            "action_hold_reasons": dict(reasons),
            "implemented_action_holds": bad,
            "multiple_action_ids_per_economic_key": duplicates,
            "quality_exceptions": quality,
            "wall_seconds": time.monotonic() - began,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "strategy_or_pnl_evaluated": False,
            "market_scope": q["scope"],
        }
    )
    db.close()
    result["digest"] = digest(result)
    write(a.out / "relations.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "counts",
                    "action_economic_terms_qualified",
                    "action_hold_reasons",
                    "wall_seconds",
                    "peak_rss_kib",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
