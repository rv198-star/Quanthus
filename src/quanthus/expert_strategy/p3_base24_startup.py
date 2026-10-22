"""Input-only clock census for P3 startup, without selecting prices or outcomes.

This diagnoses an already published P10 closure. It neither repairs clocks nor
grants historical use. A later acquisition is not itself proof of economic
lookahead: the missing per-family qualification remains a P10 responsibility.
No warmup market rows are read before authoritative membership is established.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native

PLAN_SCHEMA = "quanthus.p3-base24-startup-clock-plan.v1"
SCHEMA = "quanthus.p3-base24-startup-clock-assessment.v1"
PURPOSE = "published_input_clock_assessment_only"
MARKET_TABLES = {"dwd_security_bar", "dwd_tradability_state"}
START = "TIMESTAMPTZ '2025-01-01 00:00:00+08'"
END = "TIMESTAMPTZ '2026-09-18 15:00:00+08'"
MAX_RSS_BYTES = 4 * 1024**3


def _budget(temp_parent: Path, deadline: float) -> None:
    native._deadline(deadline)
    if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > MAX_RSS_BYTES:
        raise native.InputError("STARTUP_RSS_BUDGET_EXCEEDED")
    if shutil.disk_usage(temp_parent).free < 1024**3:
        raise native.InputError("STARTUP_FREE_SPACE_EXCEEDED")


def _validate_plan(plan: dict[str, Any], source_sha: str) -> None:
    expected = {
        "schema_version": PLAN_SCHEMA,
        "purpose": PURPOSE,
        "request_digest": native.REQUEST,
        "source_manifest_sha256": source_sha,
        "implementation_sha256": native._sha(Path(__file__)),
        "native_implementation_sha256": native._sha(Path(native.__file__)),
        "scope": native.SCOPE,
        "input_clock_read_authorized": True,
        "warmup_market_rows_read_authorized": False,
        "strategy_execution_authorized": False,
    }
    if any(type(plan.get(k)) is not type(v) or plan[k] != v for k, v in expected.items()):
        raise native.InputError("STARTUP_CLOCK_PLAN_DIFFERS")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", str(plan.get("code_revision")))
        or not re.fullmatch(r"P3-BASE24-INPUT-[A-Za-z0-9-]+", str(plan.get("input_scope_id")))
        or not isinstance(plan.get("authority_reference"), str)
        or not plan["authority_reference"].strip()
        or type(plan.get("wall_seconds")) is not int
        or not 1 <= plan["wall_seconds"] <= 900
    ):
        raise native.InputError("STARTUP_CLOCK_AUTHORITY_OR_BUDGET_REQUIRED")


def _scope(table: str) -> str:
    if table in native.INTERVAL_TABLES:
        return (
            f"effective_from IS NULL OR (effective_from<={END} "
            f"AND (effective_to IS NULL OR effective_to>{START}))"
        )
    field = (
        "trade_date"
        if table == "dwd_trading_calendar"
        else "observation_start"
        if table in MARKET_TABLES
        else "affected_effective_at"
        if table == "dwd_quality_exception"
        else "effective_at"
    )
    scope = f"CAST({field} AS DATE) BETWEEN DATE '2025-01-01' AND DATE '2026-09-18'"
    if table == "dwd_security_bar":
        scope += " AND interval_size='1d' AND price_basis IN ('none','UNADJUSTED')"
    return scope


def _census(c: Any, table: str) -> dict[str, int]:
    fields = [
        "count(*)",
        "count(*) FILTER (WHERE knowledge_from IS NULL)",
        f"count(*) FILTER (WHERE knowledge_from>{END})",
        f"count(*) FILTER (WHERE knowledge_from<={START} "
        f"AND (knowledge_to IS NULL OR knowledge_to>{START}))",
        f"count(*) FILTER (WHERE knowledge_from<={END} "
        f"AND (knowledge_to IS NULL OR knowledge_to>{END}))",
    ]
    names = [
        "native_versions",
        "unknown_knowledge",
        "not_known_by_execution_end",
        "visible_at_scope_start",
        "visible_at_execution_end",
    ]
    if table in MARKET_TABLES:
        # The frozen daily profile uses Shanghai T close. This is a diagnostic
        # comparison against the retained clock, never a replacement clock.
        close = "(CAST(CAST(observation_start AS DATE) AS TIMESTAMP)+INTERVAL 15 HOUR)"
        close = f"({close} AT TIME ZONE 'Asia/Shanghai')"
        fields += [
            "count(DISTINCT (listing_id,calendar_session_id))",
            f"count(*) FILTER (WHERE knowledge_from>{close})",
            f"count(*) FILTER (WHERE knowledge_from<={close} AND knowledge_to<={close})",
            f"count(*) FILTER (WHERE knowledge_from<={close} "
            f"AND (knowledge_to IS NULL OR knowledge_to>{close}))",
        ]
        names += [
            "listing_session_keys",
            "known_after_business_close",
            "expired_by_business_close",
            "visible_at_business_close",
        ]
    row = c.execute(f"SELECT {','.join(fields)} FROM raw_{table} WHERE {_scope(table)}").fetchone()
    return dict(zip(names, row, strict=True))


def _warmup_metadata(c: Any) -> dict[str, Any]:
    # Only calendar/venue metadata, not any 2024 market observations. Neither
    # current snapshot membership nor this count may become warmup authority.
    visible = f"knowledge_from<={START} AND (knowledge_to IS NULL OR knowledge_to>{START})"
    rows = c.execute(f"""SELECT count(*),count(*) FILTER (WHERE {visible})
        FROM raw_dwd_trading_calendar WHERE trade_date>=DATE '2024-01-01'
        AND trade_date<DATE '2025-01-01'""").fetchone()
    common = c.execute(f"""SELECT count(*) FROM (
        SELECT c.trade_date FROM raw_dwd_trading_calendar c
        JOIN (SELECT * FROM raw_dwd_trading_venue WHERE {visible}) v USING(venue_id)
        WHERE c.trade_date>=DATE '2024-01-01' AND c.trade_date<DATE '2025-01-01'
        AND c.knowledge_from<={START} AND (c.knowledge_to IS NULL OR c.knowledge_to>{START})
        AND c.is_open AND v.mic_code IN ('XSHG','XSHE')
        AND CAST(v.effective_from AS DATE)<=c.trade_date
        AND (v.effective_to IS NULL OR CAST(v.effective_to AS DATE)>c.trade_date)
        GROUP BY c.trade_date HAVING count(DISTINCT v.mic_code)=2)""").fetchone()[0]
    return {
        "calendar_metadata_year": 2024,
        "native_versions": rows[0],
        "visible_calendar_versions_before_2025": rows[1],
        "common_open_dates_from_visible_versions": common,
        "exact_60_session_authority_established": False,
        "market_rows_before_2025_read": False,
        "count_is_not_membership_or_ambiguity_acceptance": True,
    }


def assess_source_clocks(
    root: Path,
    *,
    manifest_sha256: str,
    plan: dict[str, Any],
    temp_parent: Path,
) -> dict[str, Any]:
    """Hash the exact native files once; project only input clocks and identifiers."""
    _validate_plan(plan, manifest_sha256)
    root, temp_parent = native.physical(root), native.physical(temp_parent)
    if root.is_relative_to(temp_parent) or temp_parent.is_relative_to(root):
        raise native.InputError("STARTUP_TEMP_SOURCE_OVERLAP")
    if not temp_parent.is_dir() or shutil.disk_usage(temp_parent).free < 2 * 1024**3:
        raise native.InputError("STARTUP_TEMP_SPACE_REQUIRED")
    began = time.monotonic()
    deadline = began + plan["wall_seconds"]
    metadata = native.source_metadata(root, manifest_sha256)
    files = metadata["native_closure"]["files"]
    watched = set(files) | {"manifest.json", "READY", "native-closure.json"}
    before = {name: native._fingerprint(native._member(root, name)) for name in watched}
    for name, entry in sorted(files.items()):
        _budget(temp_parent, deadline)
        native._check_member(native._member(root, name), entry, deadline=deadline)
    hash_seconds = time.monotonic() - began
    with tempfile.TemporaryDirectory(prefix="p3-clock-", dir=temp_parent) as tmp:
        raw = native._connect(root, Path(tmp))
        timer = threading.Timer(max(0, deadline - time.monotonic()), raw.interrupt)
        timer.daemon = True
        timer.start()
        try:
            c = native._BoundedConnection(raw, deadline)
            native._schema(c)
            tables = {}
            for table in native.KEYS:
                _budget(temp_parent, deadline)
                tables[table] = _census(c, table)
                _budget(temp_parent, deadline)
            warmup = _warmup_metadata(c)
            native._deadline(deadline)
        finally:
            timer.cancel()
            timer.join()
            raw.close()
    after = {name: native._fingerprint(native._member(root, name)) for name in watched}
    if before != after or native._sha(root / "manifest.json") != manifest_sha256:
        raise native.InputError("STARTUP_SOURCE_CHANGED")
    native._deadline(deadline)
    return {
        "schema_version": SCHEMA,
        "input_scope_id": plan["input_scope_id"],
        "plan_digest": digest_value(plan),
        "source_manifest_sha256": manifest_sha256,
        "code_revision": plan["code_revision"],
        "scope": native.SCOPE,
        "native_file_hashes_verified": len(files),
        "native_bytes_hashed": metadata["native_bytes"],
        "tables": tables,
        "row_census_semantics": (
            "all_source_venues; native_versions_not_accepted_P3_denominators; "
            "clock_and_identifier_columns_only; no_candidate_or_execution_qualification"
        ),
        "warmup_metadata": warmup,
        "source_fingerprints_unchanged": True,
        "price_or_outcome_columns_selected": False,
        "retained_clock_census_is_economic_PIT_qualification": False,
        "consumer_admission": "NOT_GRANTED",
        "B_accepted": False,
        "D_activated": False,
        "physical_hash_seconds": round(hash_seconds, 3),
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--temp-parent", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = assess_source_clocks(
            args.root,
            manifest_sha256=args.manifest_sha256,
            plan=native._json(native.physical(args.plan)),
            temp_parent=args.temp_parent,
        )
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "reason": str(exc) if isinstance(exc, native.InputError) else type(exc).__name__,
            "B_accepted": False,
            "D_activated": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
