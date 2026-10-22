"""Evidence-bound closure for the two exact Base 2.4 native-5m limitations."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from .base22 import file_sha

CONTRACT = "p10-base24-native5m-bounded-limitations-v1"
PRODUCT_STATUS = "PASS_WITH_BOUNDED_5M_LIMITATIONS"
LEDGER_CONTRACT = "p10-base24-native5m-coverage-ledger-package-v1"
SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}")


class Base24BoundedLimitationError(RuntimeError):
    """A bounded native-5m admission rule failed closed."""


def _object(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise Base24BoundedLimitationError(reason) from exc
    if not isinstance(value, dict):
        raise Base24BoundedLimitationError(reason)
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise Base24BoundedLimitationError("BASE24_BOUNDED_SHA256_INVALID")
    return value if value.startswith("sha256:") else "sha256:" + value


def _key(row: dict[str, Any], reason: str) -> tuple[str, str, str]:
    venue, stock_code, trade_date = (
        row.get("venue"),
        row.get("stock_code"),
        row.get("trade_date"),
    )
    if (
        venue not in {"SSE", "SZSE"}
        or not isinstance(stock_code, str)
        or re.fullmatch(r"[0-9]{6}", stock_code) is None
        or not isinstance(trade_date, str)
    ):
        raise Base24BoundedLimitationError(reason)
    try:
        parsed = date.fromisoformat(trade_date)
    except ValueError as exc:
        raise Base24BoundedLimitationError(reason) from exc
    if parsed.isoformat() != trade_date:
        raise Base24BoundedLimitationError(reason)
    return venue, stock_code, trade_date


def _rows(value: object, reason: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise Base24BoundedLimitationError(reason)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise Base24BoundedLimitationError(reason)
        key = _key(item, reason)
        if key in seen:
            raise Base24BoundedLimitationError(reason)
        seen.add(key)
        rows.append(item)
    return rows


def validate_bounded_policy(path: Path) -> dict[str, Any]:
    """Validate the exact successor policy; no broad tolerance is permitted."""
    policy = _object(path, "BASE24_BOUNDED_POLICY_INVALID")
    qualified = _rows(
        policy.get("semantic_qualifications"), "BASE24_BOUNDED_QUALIFICATION_SET_INVALID"
    )
    unavailable = _rows(
        policy.get("unavailable_security_days"), "BASE24_BOUNDED_UNAVAILABLE_SET_INVALID"
    )
    qualified_keys = {_key(row, "BASE24_BOUNDED_QUALIFICATION_SET_INVALID") for row in qualified}
    unavailable_keys = {_key(row, "BASE24_BOUNDED_UNAVAILABLE_SET_INVALID") for row in unavailable}
    rules = policy.get("consumer_rules")
    if (
        policy.get("contract_id") != CONTRACT
        or policy.get("schema_version") != 1
        or policy.get("business_timezone") != "Asia/Shanghai"
        or policy.get("required_intervals") != ["1d", "5m"]
        or policy.get("one_minute_disposition") != "NOT_INCLUDED_DIAGNOSTIC_ONLY"
        or policy.get("global_quality_thresholds_changed") is not False
        or policy.get("product_status") != PRODUCT_STATUS
        or qualified_keys & unavailable_keys
        or len(qualified_keys) != 2
        or len(unavailable_keys) != 2
        or not isinstance(rules, dict)
        or rules.get("denominator_retains_unavailable_units") is not True
        or rules.get("unavailable_rows_may_be_imputed") is not False
        or rules.get("unrelated_qualified_security_days_blocked") is not False
        or rules.get("continuous_session_rows_may_support_whole_day_turnover_features")
        is not False
        or rules.get("trading_execution_authorized") is not False
    ):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_POLICY_INVALID")
    for row in qualified:
        if (
            row.get("predecessor_reason") != "DAILY_VOLUME_OR_AMOUNT_MISMATCH"
            or row.get("disposition") != "R2_CONTINUOUS_SESSION_ONLY"
            or row.get("ledger_reason") != "native_48_bucket_grid_continuous_session_only"
            or row.get("warning")
            != "CALL_AUCTION_TURNOVER_EXCLUDED_WHOLE_DAY_TURNOVER_FEATURES_FORBIDDEN"
        ):
            raise Base24BoundedLimitationError("BASE24_BOUNDED_QUALIFICATION_RULE_INVALID")
    for row in unavailable:
        if (
            row.get("predecessor_reason") != "DAILY_PRICE_BASIS_MISMATCH"
            or row.get("ledger_reason") != "known_source_conflict_fail_closed"
            or row.get("disposition") != "UNAVAILABLE_5M"
            or row.get("daily_frequency_available") is not True
            or row.get("five_minute_fail_closed") is not True
        ):
            raise Base24BoundedLimitationError("BASE24_BOUNDED_UNAVAILABLE_RULE_INVALID")
    predecessor = policy.get("predecessor")
    evidence = policy.get("evidence")
    if not isinstance(predecessor, dict) or not isinstance(evidence, dict):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_IDENTITY_INVALID")
    for value in (
        predecessor.get("ledger_manifest_sha256"),
        predecessor.get("remaining_r3_sha256"),
        evidence.get("daily_arbitration_sha256"),
        evidence.get("dual_grid_aggregation_sha256"),
    ):
        _sha(value)
    return policy


def validate_bounded_predecessor(
    *,
    policy_path: Path,
    ledger_root: Path,
    daily_arbitration_path: Path,
    dual_grid_path: Path,
) -> dict[str, Any]:
    """Bind all four predecessor R3 keys and their safe evidence to one decision."""
    policy = validate_bounded_policy(policy_path)
    predecessor = policy["predecessor"]
    evidence = policy["evidence"]
    paths = {
        "ledger_manifest": ledger_root / "manifest.json",
        "remaining_r3": ledger_root / "remaining-r3.safe.json",
        "coverage_ledger": ledger_root / "coverage-ledger.parquet",
        "daily_arbitration": daily_arbitration_path,
        "dual_grid": dual_grid_path,
    }
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_EVIDENCE_MISSING")
    expected_hashes = {
        "ledger_manifest": predecessor["ledger_manifest_sha256"],
        "remaining_r3": predecessor["remaining_r3_sha256"],
        "daily_arbitration": evidence["daily_arbitration_sha256"],
        "dual_grid": evidence["dual_grid_aggregation_sha256"],
    }
    if any(file_sha(paths[name]) != _sha(expected) for name, expected in expected_hashes.items()):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_EVIDENCE_CHANGED")
    result = _object(ledger_root / "result.safe.json", "BASE24_BOUNDED_LEDGER_INVALID")
    if any(
        result.get(name) != predecessor.get(name)
        for name in (
            "required_security_days",
            "observed_R2_security_days",
            "justified_no_data_security_days",
            "R3_security_days",
        )
    ):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_CENSUS_CHANGED")
    remaining = json.loads(paths["remaining_r3"].read_bytes())
    if not isinstance(remaining, list):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_REMAINING_SET_INVALID")
    safe_rows = _rows(remaining, "BASE24_BOUNDED_REMAINING_SET_INVALID")
    safe = {_key(row, "BASE24_BOUNDED_REMAINING_SET_INVALID"): row for row in safe_rows}
    expected = {
        _key(row, "BASE24_BOUNDED_QUALIFICATION_SET_INVALID"): row
        for row in policy["semantic_qualifications"]
    } | {
        _key(row, "BASE24_BOUNDED_UNAVAILABLE_SET_INVALID"): row
        for row in policy["unavailable_security_days"]
    }
    if set(safe) != set(expected):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_REMAINING_SET_CHANGED")
    for key, row in expected.items():
        if safe[key].get("reason_counts") != {row["predecessor_reason"]: 1}:
            raise Base24BoundedLimitationError("BASE24_BOUNDED_PREDECESSOR_REASON_CHANGED")
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT venue,stock_code,strftime(trade_date,'%Y-%m-%d'),disposition,rating,reason
            FROM read_parquet(?) WHERE rating='R3' ORDER BY 1,2,3
            """,
            [str(paths["coverage_ledger"])],
        ).fetchall()
    finally:
        connection.close()
    ledger = {(row[0], row[1], row[2]): row[3:] for row in rows}
    if set(ledger) != set(expected) or any(
        value != ("UNRESOLVED", "R3", "missing_despite_tradable_status")
        for value in ledger.values()
    ):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_R3_SET_CHANGED")
    return {
        "contract": CONTRACT,
        "status": PRODUCT_STATUS,
        "policy_sha256": file_sha(policy_path),
        "predecessor_manifest_sha256": file_sha(paths["ledger_manifest"]),
        "required_security_days": predecessor["required_security_days"],
        "semantic_qualified_security_days": len(policy["semantic_qualifications"]),
        "bounded_unavailable_5m_security_days": len(policy["unavailable_security_days"]),
        "daily_frequency_unavailable_security_days": 0,
        "one_minute_included": False,
        "source_values_exported": False,
        "consumer_admission": "GAP_AWARE_NON_EXECUTION_ONLY",
    }


def validate_bounded_ledger_package(
    *, root: Path, manifest: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Validate one self-contained successor ledger without reopening evidence roots."""
    if manifest.get("contract") != LEDGER_CONTRACT:
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_CONTRACT_INVALID")
    if manifest.get("status") == "PASS" and result.get("status") == "PASS":
        if result.get("R3_security_days") != 0:
            raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_CENSUS_INVALID")
        return {"status": "PASS", "bounded_unavailable_keys": [], "policy_sha256": None}
    if manifest.get("status") != PRODUCT_STATUS or result.get("status") != PRODUCT_STATUS:
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_STATUS_INVALID")
    policy_path = root / "bounded-limitations.v1.json"
    receipt_path = root / "bounded-acceptance.safe.json"
    ledger_path = root / "coverage-ledger.parquet"
    package_paths = (policy_path, receipt_path, ledger_path)
    if any(path.is_symlink() or not path.is_file() for path in package_paths):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_EVIDENCE_MISSING")
    policy = validate_bounded_policy(policy_path)
    receipt = _object(receipt_path, "BASE24_BOUNDED_LEDGER_RECEIPT_INVALID")
    if (
        result.get("policy_sha256") != file_sha(policy_path)
        or receipt.get("policy_sha256") != file_sha(policy_path)
        or receipt.get("status") != PRODUCT_STATUS
        or result.get("bounded_unavailable_5m_security_days") != 2
        or result.get("R3_security_days") != 2
        or result.get("one_minute_included") is not False
    ):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_RECEIPT_INVALID")
    qualified = {
        _key(row, "BASE24_BOUNDED_QUALIFICATION_SET_INVALID"): row
        for row in policy["semantic_qualifications"]
    }
    unavailable = {
        _key(row, "BASE24_BOUNDED_UNAVAILABLE_SET_INVALID"): row
        for row in policy["unavailable_security_days"]
    }
    connection = duckdb.connect()
    try:
        census = connection.execute(
            """
            SELECT count(*),count(DISTINCT (venue,stock_code,trade_date)),
                   count_if(rating='R2'),count_if(disposition='JUSTIFIED_NO_DATA'),
                   count_if(rating='R3')
            FROM read_parquet(?)
            """,
            [str(ledger_path)],
        ).fetchone()
        scoped = connection.execute(
            """
            SELECT venue,stock_code,strftime(trade_date,'%Y-%m-%d'),
                   disposition,rating,reason,evidence_warning
            FROM read_parquet(?)
            WHERE rating='R3' OR reason='native_48_bucket_grid_continuous_session_only'
            ORDER BY 1,2,3
            """,
            [str(ledger_path)],
        ).fetchall()
    finally:
        connection.close()
    if census is None:
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_CENSUS_INVALID")
    total, unique, r2, justified, r3 = map(int, census)
    if (
        total != unique
        or total != result.get("required_security_days")
        or r2 != result.get("observed_R2_security_days")
        or justified != result.get("justified_no_data_security_days")
        or r3 != result.get("R3_security_days")
    ):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_CENSUS_INVALID")
    actual = {(row[0], row[1], row[2]): row[3:] for row in scoped}
    if set(actual) != set(qualified) | set(unavailable):
        raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_SCOPED_SET_INVALID")
    for key, rule in qualified.items():
        if actual[key] != (
            "OBSERVED",
            "R2",
            rule["ledger_reason"],
            rule["warning"],
        ):
            raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_QUALIFICATION_INVALID")
    for key, rule in unavailable.items():
        if actual[key] != (
            "UNRESOLVED",
            "R3",
            rule["ledger_reason"],
            "EXACT_5M_SECURITY_DAY_UNAVAILABLE_DAILY_REMAINS_AVAILABLE",
        ):
            raise Base24BoundedLimitationError("BASE24_BOUNDED_LEDGER_UNAVAILABLE_INVALID")
    return {
        "status": PRODUCT_STATUS,
        "bounded_unavailable_keys": [list(key) for key in sorted(unavailable)],
        "policy_sha256": file_sha(policy_path),
    }
