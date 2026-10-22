"""P10's bounded, result-blind qualification of a retrospective P3 snapshot.

The original strict NativeView remains unchanged. Late warehouse acquisition
does not reject this weaker input purpose and never becomes a historical clock.
The output is safe aggregate evidence, not a B/D activation or strategy result.
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native

SCHEMA = "quanthus.p10-p3-retrospective-assessment.v1"
POLICY = "config/research/p3-strat-003/base24-retrospective-purpose.v1.json"
POLICY_SCHEMA = "quanthus.p3-base24-retrospective-purpose.v1"
GRADE = "R2_RETROSPECTIVE_SNAPSHOT_NOT_HISTORICAL_PIT"


def snapshot_query(table: str, cutoff: datetime) -> str:
    """Select original snapshot versions, never invent a T-close timestamp."""
    if table not in native.KEYS or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise native.InputError("PURPOSE_SNAPSHOT_SELECTION")
    clock = "TIMESTAMPTZ " + native._q(cutoff.isoformat())
    return (
        f"SELECT DISTINCT * FROM raw_{table} WHERE knowledge_from<={clock} "
        f"AND (knowledge_to IS NULL OR knowledge_to>{clock})"
    )


def load_policy(repository: Path) -> dict[str, Any]:
    policy = native._json(repository / POLICY)
    if (
        policy.get("schema_version") != POLICY_SCHEMA
        or policy.get("purpose_id") != "p3-base24-retrospective-input-v1"
        or policy.get("original_request_digest") != native.REQUEST
        or policy.get("original_strict_request_replaced") is not False
        or policy.get("input_grade") != GRADE
        or policy.get("business_scope") != native.SCOPE
        or policy.get("information_cutoff")
        != "exact source manifest knowledge_cutoff; never historical T close"
        or policy.get("allowed")
        != [
            "result-blind source and input qualification",
            "new job-local retrospective input preparation",
        ]
        or "D result execution" not in policy.get("not_authorized_by_this_contract", [])
    ):
        raise native.InputError("PURPOSE_POLICY_DIFFERS")
    return policy


def _one(c: Any, sql: str) -> int:
    return int(c.execute(sql).fetchone()[0])


def _snapshot(c: Any, cutoff: datetime) -> None:
    for table in native.KEYS:
        query = snapshot_query(table, cutoff)
        native._unambiguous(c, query, table, "PURPOSE_AMBIGUOUS_SNAPSHOT_VERSION")
        c.execute(f"CREATE OR REPLACE TEMP VIEW {table} AS {query}")


def key_lineage_counts(c: Any, table: str) -> tuple[int, int]:
    bad_lineage = _one(
        c,
        f"SELECT count(*) FROM {table} WHERE "
        "source_record_id IS NULL OR source_record_id='' OR "
        "source_revision IS NULL OR source_revision='' OR "
        "source_artifact_sha256 IS NULL OR NOT regexp_full_match("
        "source_artifact_sha256,'sha256:[0-9a-f]{64}')",
    )
    bad_key = _one(
        c,
        f"SELECT count(*) FROM {table} WHERE "
        + " OR ".join(f"{key} IS NULL" for key in native.KEYS[table]),
    )
    return bad_lineage, bad_key


def calendar_identity_counts(c: Any, warmup: dict[str, Any]) -> dict[str, int]:
    """Same physical date/venue and exact-membership checks for build and cold read."""
    result = {"warmup_membership_mismatch": 0}
    for session in native._warmup_binding(warmup):
        for mic in ("XSHG", "XSHE"):
            count = _one(
                c,
                "SELECT count(*) FROM dwd_trading_calendar c JOIN "
                "dwd_trading_venue v USING(venue_id) WHERE c.is_open AND "
                f"c.trade_date=DATE {native._q(session['trade_date'])} AND "
                f"v.mic_code={native._q(mic)} AND "
                f"c.calendar_session_id={native._q(session[mic])}",
            )
            result["warmup_membership_mismatch"] += int(count != 1)
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        result[table] = _one(
            c,
            f"SELECT count(*) FROM {table} b WHERE NOT EXISTS "
            "(SELECT 1 FROM dwd_trading_calendar c JOIN dwd_listing l "
            "ON c.venue_id=l.venue_id WHERE c.calendar_session_id=b.calendar_session_id "
            "AND c.trade_date=CAST(b.observation_start AS DATE) "
            "AND l.listing_id=b.listing_id AND l.effective_from<=b.observation_start "
            "AND (l.effective_to IS NULL OR l.effective_to>b.observation_start))",
        )
    return result


def reference_counts(c: Any) -> dict[str, int]:
    result = {}
    for child, field, parent, target, nullable in native.REFERENCES:
        guard = f"x.{field} IS NOT NULL AND " if nullable else ""
        result[f"{child}:{field}"] = _one(
            c,
            f"SELECT count(*) FROM {child} x WHERE {guard}NOT EXISTS "
            f"(SELECT 1 FROM {parent} p WHERE p.{target}=x.{field})",
        )
    return result


def assess(
    root: Path,
    *,
    manifest_sha256: str,
    repository: Path,
    warmup: dict[str, Any],
    temp_parent: Path,
) -> dict[str, Any]:
    """One physical verification and one scoped, bounded census. No price output."""
    policy = load_policy(repository)
    dates = [s["trade_date"] for s in native._warmup_binding(warmup)]
    root, temp_parent = native.physical(root), native.physical(temp_parent)
    native._isolate(root, temp_parent, temp_parent)
    budget = policy["budgets"]
    if (
        not temp_parent.is_dir()
        or shutil.disk_usage(temp_parent).free < budget["minimum_free_bytes"]
    ):
        raise native.InputError("PURPOSE_SCRATCH_SPACE_REQUIRED")
    began = time.monotonic()
    deadline = began + budget["wall_seconds"]
    metadata = native.source_metadata(root, manifest_sha256)
    cutoff = datetime.fromisoformat(metadata["source_manifest"]["knowledge_cutoff"])
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise native.InputError("PURPOSE_SOURCE_CLOCK_REQUIRED")
    files = metadata["native_closure"]["files"]
    watched = sorted(set(files) | {"manifest.json", "READY", "native-closure.json"})
    before = {n: native._fingerprint(native._member(root, n)) for n in watched}
    for name, entry in sorted(files.items()):
        native._check_member(native._member(root, name), entry, deadline=deadline)
    hash_seconds = time.monotonic() - began
    failures: list[str] = []
    summary: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="p3-purpose-", dir=temp_parent) as tmp:
        raw = native._connect(root, Path(tmp))
        timer = threading.Timer(max(0, deadline - time.monotonic()), raw.interrupt)
        timer.daemon = True
        timer.start()
        try:
            c = native._BoundedConnection(raw, deadline)
            schemas = native._schema(c)
            # Bound every native family before any row-level analysis. 2024
            # market observations are only the exact, independently bound 60 days.
            for table in native.KEYS:
                predicate = native._predicate(table, dates)
                if table == "dwd_security_bar":
                    predicate += " AND interval_size='1d' AND price_basis IN ('none','UNADJUSTED')"
                path = native._member(root, f"native/{table}.parquet")
                c.execute(
                    f"CREATE OR REPLACE TEMP VIEW raw_{table} AS "
                    f"SELECT * FROM read_parquet({native._q(path)}) WHERE {predicate}"
                )
            _snapshot(c, cutoff)
            for table in native.KEYS:
                known, unknown, selected = (
                    _one(c, f"SELECT count(*) FROM raw_{table}"),
                    _one(c, f"SELECT count(*) FROM raw_{table} WHERE knowledge_from IS NULL"),
                    _one(c, f"SELECT count(*) FROM {table}"),
                )
                bad_lineage, bad_key = key_lineage_counts(c, table)
                if bad_lineage or bad_key:
                    failures.append("PURPOSE_KEY_OR_LINEAGE:" + table)
                summary[table] = {
                    "retained_versions": known,
                    "unknown_knowledge": unknown,
                    "snapshot_rows": selected,
                    "invalid_lineage": bad_lineage,
                    "null_keys": bad_key,
                    "schema_digest": digest_value(schemas[table]),
                }
            identity = calendar_identity_counts(c, warmup)
            if identity["warmup_membership_mismatch"]:
                failures.append("PURPOSE_WARMUP_MEMBERSHIP")
            for table in ("dwd_security_bar", "dwd_tradability_state"):
                summary[table]["date_venue_identity_mismatch"] = identity[table]
                if identity[table]:
                    failures.append("PURPOSE_DATE_VENUE_IDENTITY:" + table)
            classes = c.execute(
                "SELECT count(*), count(*) FILTER (WHERE volume=0), "
                "count(*) FILTER (WHERE volume IS NULL OR turnover IS NULL), "
                "count(*) FILTER (WHERE open IS NULL OR high IS NULL OR low IS NULL "
                "OR close IS NULL OR open<=0 OR close<=0 OR low<=0 OR high<low "
                "OR open NOT BETWEEN low AND high OR close NOT BETWEEN low AND high), "
                "count(*) FILTER (WHERE knowledge_from>observation_end) "
                "FROM dwd_security_bar"
            ).fetchone()
            summary["dwd_security_bar"]["classes"] = dict(
                zip(
                    (
                        "snapshot_rows",
                        "zero_volume",
                        "null_liquidity",
                        "invalid_price",
                        "warehouse_known_after_observation_end",
                    ),
                    classes,
                    strict=True,
                )
            )
            summary["corporate_action_scope"] = {
                "unknown_action_versions": summary["dwd_corporate_action"]["unknown_knowledge"],
                "unknown_term_versions": summary["dwd_corporate_action_term"]["unknown_knowledge"],
                "unknown_means_no_action": False,
                "execution_support_qualified_by_this_assessment": False,
            }
            summary["reference_defects"] = reference_counts(c)
            native._deadline(deadline)
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 > budget["rss_bytes"]:
                raise native.InputError("PURPOSE_RSS_BUDGET_EXCEEDED")
        finally:
            timer.cancel()
            timer.join()
            raw.close()
    after = {n: native._fingerprint(native._member(root, n)) for n in watched}
    if before != after or native._sha(root / "manifest.json") != manifest_sha256:
        raise native.InputError("PURPOSE_SOURCE_CHANGED")
    return {
        "schema_version": SCHEMA,
        "owner_line": "P10",
        "purpose_id": policy["purpose_id"],
        "policy_digest": digest_value(policy),
        "source_manifest_sha256": manifest_sha256,
        "implementation_sha256": native._sha(Path(__file__)),
        "native_implementation_sha256": native._sha(Path(native.__file__)),
        "warmup_digest": digest_value(warmup),
        "information_cutoff": cutoff.isoformat(),
        "business_scope": native.SCOPE,
        "input_grade": GRADE,
        "status": "BLOCKED_CRITICAL_INPUT"
        if failures
        else "RETROSPECTIVE_SOURCE_CENSUS_PASS_WITH_LIMITATIONS",
        "critical_failures": sorted(set(failures)),
        "tables": summary,
        "native_files_verified": len(files),
        "native_bytes_hashed": metadata["native_bytes"],
        "source_fingerprints_unchanged": True,
        "historical_visibility_proven": False,
        "original_4173_policy_preserved_not_remeasured": True,
        "original_B_accepted": False,
        "D_activated": False,
        "no_strategy_or_economic_results": True,
        "source_network_requests": 0,
        "physical_hash_seconds": round(hash_seconds, 3),
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--warmup", type=Path, required=True)
    parser.add_argument("--temp-parent", type=Path, required=True)
    args = parser.parse_args(argv)
    result = assess(
        args.root,
        manifest_sha256=args.manifest_sha256,
        repository=args.repository,
        warmup=native._json(args.warmup),
        temp_parent=args.temp_parent,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result["critical_failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
