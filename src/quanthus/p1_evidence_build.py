"""Rebuild Chapter 01 acceptance evidence from one immutable local snapshot.

The builder never mutates canonical market data.  It makes the D1/D2/D3 replay
evidence reproducible after a new Hermes revision is pulled into OCI, while
leaving the release-gate decision to the separately versioned gate file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.data_foundation import PublishedSnapshot


class EvidenceBuildError(RuntimeError):
    """Raised when a P1 evidence package cannot be revalidated honestly."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceBuildError(f"required evidence input does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceBuildError(f"invalid JSON in evidence input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceBuildError(f"evidence input must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _parquet_source(path: Path) -> str:
    return f"read_parquet({_sql_string(str(path))})"


def _as_cases(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise EvidenceBuildError(f"{field_name} must be an array of objects")
    return [dict(item) for item in value]


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceBuildError(f"{field_name} must be a non-empty string")
    return value


def _case_date(case: dict[str, Any], field_name: str) -> date:
    value = _required_string(case.get(field_name), field_name)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceBuildError(f"{field_name} must be ISO-8601: {value!r}") from exc


def _case_code(case: dict[str, Any]) -> str:
    return _required_string(case.get("security_id"), "security_id")


def _snapshot_dataset(
    snapshot_dir: Path, snapshot: PublishedSnapshot, dataset: str
) -> tuple[Path, str]:
    manifests = [manifest for manifest in snapshot.datasets if manifest.dataset == dataset]
    if len(manifests) != 1:
        raise EvidenceBuildError(f"snapshot must contain exactly one {dataset!r} dataset")
    manifest = manifests[0]
    if len(manifest.partitions) != 1 or len(manifest.content_checksums) != 1:
        raise EvidenceBuildError(f"snapshot dataset {dataset!r} must have exactly one partition")
    partition = manifest.partitions[0]
    checksum = manifest.content_checksums.get(partition)
    if checksum is None:
        raise EvidenceBuildError(f"snapshot dataset {dataset!r} has no checksum for its partition")
    path = snapshot_dir / partition
    if not path.is_file():
        raise EvidenceBuildError(f"snapshot dataset {dataset!r} is missing: {path}")
    if _sha256(path) != checksum:
        raise EvidenceBuildError(f"snapshot dataset {dataset!r} checksum does not match manifest")
    return path, checksum


def _load_snapshot(snapshot_dir: Path) -> PublishedSnapshot:
    raw = _load_object(snapshot_dir / "snapshot.json")
    try:
        return PublishedSnapshot.from_dict(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceBuildError(f"invalid published snapshot: {snapshot_dir}") from exc


def _duckdb_connection() -> Any:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise EvidenceBuildError("P1 dependencies are missing; run `make bootstrap-p1`") from exc
    return duckdb.connect(":memory:")


def _pairs_cte(cases: list[dict[str, Any]], date_field: str, name: str) -> str:
    values: list[str] = []
    for case in cases:
        code = _case_code(case)
        market_date = _case_date(case, date_field)
        values.append(f"({_sql_string(code)}, DATE {_sql_string(market_date.isoformat())})")
    return f"{name}(stock_code, market_date) AS (VALUES {', '.join(values)})"


def _same_number(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is expected
    try:
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _revalidate_d1(
    daily_none: Path, pack_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_cases = _as_cases(json.loads(pack_path.read_text(encoding="utf-8")), "D1 point compare")
    pairs = {(_case_code(case), _case_date(case, "market_date")) for case in source_cases}
    dates = {market_date for _, market_date in pairs}
    codes = {code for code, _ in pairs}
    if len(source_cases) != 100 or len(pairs) != 100 or len(dates) != 10 or len(codes) != 10:
        raise EvidenceBuildError(
            "D1 evidence must contain an exact 10 dates x 10 securities matrix"
        )

    connection = _duckdb_connection()
    try:
        rows = connection.execute(
            f"""
            WITH {_pairs_cte(source_cases, "market_date", "requested")}
            SELECT b.stock_code, CAST(b.date AS DATE), b.open, b.high, b.low, b.close,
                   b.volume, b.amount
            FROM {_parquet_source(daily_none)} b
            JOIN requested r
              ON b.stock_code = r.stock_code
             AND CAST(b.date AS DATE) = r.market_date
            """
        ).fetchall()
    finally:
        connection.close()
    current = {
        (str(row[0]), row[1]): {
            "open": row[2],
            "high": row[3],
            "low": row[4],
            "close": row[5],
            "volume": row[6],
            "amount": row[7],
        }
        for row in rows
    }
    fields = ("open", "high", "low", "close", "volume", "amount")
    rebuilt: list[dict[str, Any]] = []
    for case in sorted(
        source_cases, key=lambda item: (_case_date(item, "market_date"), _case_code(item))
    ):
        code = _case_code(case)
        market_date = _case_date(case, "market_date")
        observed = current.get((code, market_date))
        reference = case.get("ref")
        if not isinstance(reference, dict):
            raise EvidenceBuildError(f"D1 case is missing independent values: {code}/{market_date}")
        matches = observed is not None and all(
            _same_number(observed.get(field), reference.get(field)) for field in fields
        )
        rebuilt.append(
            {
                "market_date": market_date.isoformat(),
                "security_id": code,
                "status": "pass" if matches else "fail",
                "reference_id": _required_string(case.get("reference_id"), "D1.reference_id"),
            }
        )
    if any(case["status"] != "pass" for case in rebuilt):
        raise EvidenceBuildError("D1 revalidation does not match the independent Baostock pack")
    return rebuilt, {
        "path": str(pack_path),
        "sha256": _sha256(pack_path),
        "cases": len(rebuilt),
        "dates": len(dates),
        "securities": len(codes),
    }


def _revalidate_d2(
    coverage_path: Path,
    suspensions_path: Path,
    prior_evidence: dict[str, Any],
    snapshot_cutoff: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    d2 = prior_evidence.get("D2")
    if not isinstance(d2, dict):
        raise EvidenceBuildError("prior evidence is missing D2 cases")
    source_cases = _as_cases(d2.get("cases"), "prior D2.cases")
    if len(source_cases) < 20:
        raise EvidenceBuildError("D2 requires at least 20 cases")
    case_keys = {(_case_code(case), _case_date(case, "market_date")) for case in source_cases}
    if len(case_keys) != len(source_cases):
        raise EvidenceBuildError("D2 source cases contain duplicate security/date pairs")
    suspension_ids: set[str] = set()
    for case in source_cases:
        reference_id = _required_string(case.get("reference_id"), "D2.reference_id")
        prefix, separator, suspension_id = reference_id.partition(":")
        if prefix != "suspension" or not separator or not suspension_id:
            raise EvidenceBuildError("D2 references must name a suspension_id")
        suspension_ids.add(suspension_id)

    connection = _duckdb_connection()
    try:
        coverage_rows = connection.execute(
            f"""
            WITH {_pairs_cte(source_cases, "market_date", "requested")}
            SELECT c.stock_code, CAST(c.market_date AS DATE), c.coverage_status,
                   c.bar_present, c.is_executable
            FROM {_parquet_source(coverage_path)} c
            JOIN requested r
              ON c.stock_code = r.stock_code
             AND CAST(c.market_date AS DATE) = r.market_date
            """
        ).fetchall()
        identifier_list = ", ".join(
            _sql_string(identifier) for identifier in sorted(suspension_ids)
        )
        suspension_rows = connection.execute(
            f"""
            SELECT suspension_id, stock_code, CAST(suspend_start AS DATE),
                   CAST(suspend_end AS DATE), CAST(knowledge_cutoff AS DATE)
            FROM {_parquet_source(suspensions_path)}
            WHERE suspension_id IN ({identifier_list})
            """
        ).fetchall()
    finally:
        connection.close()
    coverage = {
        (str(row[0]), row[1]): {
            "coverage_status": str(row[2]),
            "bar_present": bool(row[3]),
            "is_executable": bool(row[4]),
        }
        for row in coverage_rows
    }
    suspensions = {str(row[0]): row[1:] for row in suspension_rows}
    rebuilt: list[dict[str, Any]] = []
    for case in sorted(
        source_cases, key=lambda item: (_case_date(item, "market_date"), _case_code(item))
    ):
        code = _case_code(case)
        market_date = _case_date(case, "market_date")
        reference_id = _required_string(case.get("reference_id"), "D2.reference_id")
        suspension_id = reference_id.split(":", maxsplit=1)[1]
        record = coverage.get((code, market_date))
        suspension = suspensions.get(suspension_id)
        suspension_matches = (
            suspension is not None
            and str(suspension[0]) == code
            and suspension[1] <= market_date
            and (suspension[2] is None or suspension[2] >= market_date)
            and suspension[3] <= snapshot_cutoff
        )
        passed = (
            record is not None
            and record["coverage_status"] == "suspended"
            and not record["bar_present"]
            and not record["is_executable"]
            and suspension_matches
        )
        rebuilt.append(
            {
                "market_date": market_date.isoformat(),
                "security_id": code,
                "case_type": _required_string(case.get("case_type"), "D2.case_type"),
                "status": "pass" if passed else "fail",
                "reference_id": reference_id,
            }
        )
    if any(case["status"] != "pass" for case in rebuilt):
        raise EvidenceBuildError(
            "D2 revalidation no longer matches suspension and coverage evidence"
        )
    return rebuilt, {"cases": len(rebuilt), "coverage_path": str(coverage_path)}


def _revalidate_d3(
    daily_none: Path,
    daily_qfq: Path,
    factors_path: Path,
    seed_path: Path,
    prior_evidence: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seed = _load_object(seed_path)
    seed_cases = _as_cases(seed.get("cases"), "D3 seed cases")
    seeded = {
        (
            _required_string(case.get("stock_code"), "D3 seed stock_code"),
            _case_date(case, "effective_date"),
        ): case
        for case in seed_cases
    }
    d3 = prior_evidence.get("D3")
    if not isinstance(d3, dict):
        raise EvidenceBuildError("prior evidence is missing D3 cases")
    source_cases = _as_cases(d3.get("cases"), "prior D3.cases")
    if not source_cases:
        raise EvidenceBuildError("D3 requires at least one ex-right case")
    for case in source_cases:
        key = (_case_code(case), _case_date(case, "ex_date"))
        if key not in seeded:
            raise EvidenceBuildError(
                f"D3 case is absent from the linked factor seed: {key[0]}/{key[1]}"
            )

    connection = _duckdb_connection()
    try:
        codes = sorted({_case_code(case) for case in source_cases})
        identifiers = ", ".join(_sql_string(code) for code in codes)
        return_rows = connection.execute(
            f"""
            WITH none_rows AS (
                SELECT stock_code, CAST(date AS DATE) AS market_date, close,
                       lag(close) OVER (
                           PARTITION BY stock_code ORDER BY CAST(date AS DATE)
                       ) AS prior_close,
                       'none' AS series
                FROM {_parquet_source(daily_none)}
                WHERE stock_code IN ({identifiers})
            ), qfq_rows AS (
                SELECT stock_code, CAST(date AS DATE) AS market_date, close,
                       lag(close) OVER (
                           PARTITION BY stock_code ORDER BY CAST(date AS DATE)
                       ) AS prior_close,
                       'qfq' AS series
                FROM {_parquet_source(daily_qfq)}
                WHERE stock_code IN ({identifiers})
            )
            SELECT stock_code, market_date, close, prior_close, series
            FROM none_rows
            UNION ALL
            SELECT stock_code, market_date, close, prior_close, series
            FROM qfq_rows
            """
        ).fetchall()
        factor_rows = connection.execute(
            f"""
            SELECT stock_code, CAST(effective_date AS DATE), adjust_factor,
                   CAST(knowledge_cutoff AS DATE), visibility_origin, source_revision
            FROM {_parquet_source(factors_path)}
            WHERE stock_code IN ({identifiers})
            """
        ).fetchall()
    finally:
        connection.close()
    returns = {(str(row[0]), row[1], str(row[4])): (row[2], row[3]) for row in return_rows}
    factors = {(str(row[0]), row[1]): row[2:] for row in factor_rows}
    rebuilt: list[dict[str, Any]] = []
    for case in sorted(
        source_cases, key=lambda item: (_case_date(item, "ex_date"), _case_code(item))
    ):
        code = _case_code(case)
        ex_date = _case_date(case, "ex_date")
        none_close, none_prior = returns.get((code, ex_date, "none"), (None, None))
        qfq_close, qfq_prior = returns.get((code, ex_date, "qfq"), (None, None))
        factor = factors.get((code, ex_date))
        raw_return = (
            None if none_close is None or not none_prior else (none_close / none_prior - 1) * 100
        )
        qfq_return = (
            None if qfq_close is None or not qfq_prior else (qfq_close / qfq_prior - 1) * 100
        )
        expected_raw = case.get("raw_return_pct")
        expected_qfq = case.get("qfq_return_pct")
        factor_is_linked = (
            factor is not None
            and factor[0] is not None
            and float(factor[0]) > 0
            and factor[1] <= ex_date
            and str(factor[2]) == "corp_actions.announce_date"
            and bool(str(factor[3]).strip())
        )
        passed = (
            factor_is_linked
            and raw_return is not None
            and qfq_return is not None
            and _same_number(round(raw_return, 6), expected_raw)
            and _same_number(round(qfq_return, 6), expected_qfq)
        )
        rebuilt.append(
            {
                "ex_date": ex_date.isoformat(),
                "security_id": code,
                "status": "pass" if passed else "fail",
                "reference_id": _required_string(case.get("reference_id"), "D3.reference_id"),
                "artificial_jump_detected": not passed,
                "raw_return_pct": None if raw_return is None else round(raw_return, 6),
                "qfq_return_pct": None if qfq_return is None else round(qfq_return, 6),
            }
        )
    if any(case["status"] != "pass" for case in rebuilt):
        raise EvidenceBuildError("D3 revalidation no longer matches the linked PIT factor evidence")
    return rebuilt, {"path": str(seed_path), "sha256": _sha256(seed_path), "cases": len(rebuilt)}


def _audit_check(audit: dict[str, Any], check_id: str) -> dict[str, Any]:
    checks = audit.get("checks")
    if not isinstance(checks, list):
        raise EvidenceBuildError("data audit has no checks array")
    for check in checks:
        if isinstance(check, dict) and check.get("check_id") == check_id:
            return check
    raise EvidenceBuildError(f"data audit is missing {check_id}")


def _summary_count(report: dict[str, Any], field_name: str) -> int:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise EvidenceBuildError("report has no summary object")
    value = summary.get(field_name)
    if not isinstance(value, int):
        raise EvidenceBuildError(f"report summary has no integer {field_name}")
    return value


def build_p1_acceptance_evidence(
    *,
    snapshot_dir: Path,
    coverage_path: Path,
    coverage_metadata_path: Path,
    data_audit_path: Path,
    s1_report_path: Path,
    prior_evidence_path: Path,
    d1_pack_path: Path,
    d3_seed_path: Path,
) -> dict[str, Any]:
    """Revalidate and assemble a P1 evidence bundle without changing gate state."""

    snapshot_dir = snapshot_dir.resolve()
    snapshot = _load_snapshot(snapshot_dir)
    daily_none, none_checksum = _snapshot_dataset(snapshot_dir, snapshot, "daily_ohlcv_none")
    daily_qfq, qfq_checksum = _snapshot_dataset(snapshot_dir, snapshot, "daily_ohlcv_qfq")
    factors, factor_checksum = _snapshot_dataset(snapshot_dir, snapshot, "adjustment_factors")
    suspensions, suspension_checksum = _snapshot_dataset(snapshot_dir, snapshot, "suspensions")
    _, boundary_checksum = _snapshot_dataset(
        snapshot_dir, snapshot, "delist_day_boundary_exceptions"
    )

    coverage_metadata = _load_object(coverage_metadata_path)
    audit = _load_object(data_audit_path)
    s1_report = _load_object(s1_report_path)
    prior_evidence = _load_object(prior_evidence_path)
    expected_none_checksum = none_checksum.removeprefix("sha256:")
    if coverage_metadata.get("source_checksum") != expected_none_checksum:
        raise EvidenceBuildError("coverage metadata is not bound to the published none snapshot")
    if s1_report.get("source_checksum") != expected_none_checksum:
        raise EvidenceBuildError("S1 report is not bound to the published none snapshot")
    if audit.get("summary", {}).get("fail") != 0:
        raise EvidenceBuildError("data audit contains failures")
    factor_warning = _audit_check(audit, "adjustment_factors.batch_dated_historical_visibility")
    # Controlled residual after EM exact-unique link (Hermes adj_factors_pit_exception_ledger_v1).
    # Historical seed used 6910; post recommended-band link the fail-closed residual is 4173.
    allowed_c1_exception_counts = {4173, 6910}
    if factor_warning.get("status") != "warn" or int(factor_warning.get("count") or -1) not in (
        allowed_c1_exception_counts
    ):
        raise EvidenceBuildError(
            "C1 exception count changed outside controlled residual set {4173, 6910}; "
            "review the PIT policy before rebuilding"
        )
    c1_exception_count = int(factor_warning.get("count") or 0)
    allowed_statuses = {"trading", "suspended", "not_listed", "delisted", "missing"}
    status_counts = coverage_metadata.get("status_counts")
    if not isinstance(status_counts, dict) or set(status_counts) - allowed_statuses:
        raise EvidenceBuildError("coverage contains an unclassified lifecycle status")
    if _summary_count(s1_report, "unexplained_missing_session_count") != 0:
        raise EvidenceBuildError("S1 still has unexplained missing sessions")
    if _summary_count(s1_report, "invalid_observed_session_count") != 0:
        raise EvidenceBuildError("S1 still has invalid observed sessions")

    d1_cases, d1_metadata = _revalidate_d1(daily_none, d1_pack_path)
    d2_cases, d2_metadata = _revalidate_d2(
        coverage_path,
        suspensions,
        prior_evidence,
        snapshot.reference.knowledge_cutoff.date(),
    )
    d3_cases, d3_metadata = _revalidate_d3(
        daily_none,
        daily_qfq,
        factors,
        d3_seed_path,
        prior_evidence,
    )
    return {
        "schema_version": "1.0.0",
        "snapshot_id": snapshot.reference.snapshot_id,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "scope_note": (
            "OCI read-only revalidation; this bundle does not change "
            "config/p1-gates.v1.json or Hermes canonical data."
        ),
        "sources": {
            "published_snapshot": {
                "path": str(snapshot_dir / "snapshot.json"),
                "sha256": _sha256(snapshot_dir / "snapshot.json"),
                "market_date": snapshot.reference.market_date.isoformat(),
                "knowledge_cutoff": snapshot.reference.knowledge_cutoff.isoformat(),
            },
            "daily_ohlcv_none": {"sha256": none_checksum, "path": str(daily_none)},
            "daily_ohlcv_qfq_corroboration": {"sha256": qfq_checksum, "path": str(daily_qfq)},
            "adjustment_factors": {"sha256": factor_checksum, "path": str(factors)},
            "suspensions": {"sha256": suspension_checksum, "path": str(suspensions)},
            "delist_day_boundary_exceptions": {
                "sha256": boundary_checksum,
                "revision": "delist_day_boundary_exceptions_v1",
            },
            "D1": d1_metadata,
            "D2": d2_metadata,
            "D3": d3_metadata,
        },
        "blockers": {
            "C1": {
                "status": "open",
                "evidence_id": (
                    f"{data_audit_path}#adjustment_factors.batch_dated_historical_visibility"
                ),
                "detail": (
                    f"{c1_exception_count} batch-dated factor visibility exceptions remain "
                    "intentionally fail-closed after EM exact-unique linking; "
                    "C1 correctness = no lookahead; C1 completeness = controlled gap "
                    "(not full pass). knowledge_cutoff was never fabricated to effective_date."
                ),
            },
            "C2": {
                "status": "pass",
                "evidence_id": str(coverage_metadata_path),
                "detail": "Every in-scope security/open-session row has a lifecycle status.",
            },
            "S1": {
                "status": "pass",
                "evidence_id": str(s1_report_path),
                "detail": (
                    "No unexplained missing or invalid observed delisted-history sessions; "
                    "terminal boundaries use exact evidenced keys."
                ),
            },
        },
        "D1": {
            "independent_reference": "Baostock adjustflag=3 raw reference pack",
            "cases": d1_cases,
        },
        "D2": {
            "independent_reference": (
                "PIT suspension records reconciled against the immutable coverage matrix"
            ),
            "cases": d2_cases,
        },
        "D3": {
            "independent_reference": (
                "Linked PIT factor seed with direct adjacent none and legacy-qfq return replay"
            ),
            "cases": d3_cases,
        },
    }


def write_p1_acceptance_evidence(path: Path, evidence: dict[str, Any]) -> None:
    """Atomically write derived OCI evidence without touching canonical source data."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(evidence, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
