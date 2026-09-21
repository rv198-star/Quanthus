"""Bounded input-only industry coverage inspection for P3-STRAT-003-B-R1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from quanthus.event_library.physical_scan import (
    PhysicalEventScanError,
    _read_parquet_expression,
    _table_paths,
)
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.p3_strat_003_input_qualification import _request
from quanthus.forward_validation.activation import load_prospective_runtime_config
from quanthus.industry_mapping import IndustryMappingError, load_static_industry_mapping
from quanthus.policy import find_project_root

REQUEST_SCHEMA = "quanthus.p3-strat-003-industry-inspection.v1"
REQUEST_DIGEST = "sha256:3262e61bf8cfab5e58f049a5215dedce319ec71c6d2ec2a6855e43562364d57c"
ARTIFACT_SCHEMA = "quanthus.p3-strat-003-industry-inspection-artifact.v1"
COMPLETE = "INDUSTRY_COVERAGE_INSPECTION_COMPLETE"
UNAVAILABLE = "DIAGNOSTIC_INPUT_UNAVAILABLE"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _fail(message: str) -> NoReturn:
    raise ExpertEvaluationError("p3_strat_003_industry_inspection_invalid", message)


def _source_unavailable(
    message: str,
    *,
    cause: BaseException | None = None,
) -> NoReturn:
    error = ExpertEvaluationError(
        "p3_strat_003_industry_inspection_source_unavailable",
        message,
    )
    if cause is None:
        raise error
    raise error from cause


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(
    path: Path,
    *,
    root: Path,
    limit: int,
    label: str,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(resolved_root):
        _fail(f"{label} escapes its fixed read root")
    try:
        if resolved.stat().st_size > limit:
            _fail(f"{label} exceeds its byte limit")
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_industry_inspection_unreadable",
            label,
        ) from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _run_root() -> Path:
    return Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs")).resolve()


def _relative_child(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        _fail(f"{label} path must be non-empty text")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        _fail(f"{label} path is unsafe")
    path = (root.resolve() / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        _fail(f"{label} path escapes its fixed root")
    return path


def _candidate_inventory(
    run_root: Path,
    failed_job: dict[str, Any],
    *,
    decision_start: date,
    decision_end: date,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    relative = Path(str(failed_job["candidate_relative_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        _fail("failed candidate path is invalid")
    path = (
        run_root
        / ".staging"
        / str(failed_job["job_id"])
        / str(failed_job["claim_generation"])
        / relative
    ).resolve()
    if (
        not path.is_file()
        or path.is_symlink()
        or not path.is_relative_to(run_root.resolve())
    ):
        return {
            "status": "missing_or_unreadable",
            "path": str(path),
            "claim_execution_time_bytes_reproduced": False,
        }, []

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ExpertEvaluationError("dependency_missing", "duckdb") from exc

    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute(
            "SELECT * FROM read_parquet(?) LIMIT 0",
            [str(path)],
        )
        columns = {str(item[0]) for item in cursor.description}
        required = {
            "dwd_security_id",
            "dwd_company_id",
            "dwd_listing_id",
            "listing_code",
            "venue_id",
            "session_date",
        }
        if not required.issubset(columns):
            _fail("failed candidate projection lacks required identity columns")
        bad = connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?)
            WHERE dwd_security_id IS NULL
               OR dwd_company_id IS NULL
               OR dwd_listing_id IS NULL
               OR listing_code IS NULL
               OR venue_id IS NULL
               OR session_date IS NULL
            """,
            [str(path)],
        ).fetchone()
        duplicates = connection.execute(
            """
            SELECT count(*)
            FROM (
              SELECT dwd_security_id, session_date
              FROM read_parquet(?)
              GROUP BY ALL
              HAVING count(*) <> 1
            )
            """,
            [str(path)],
        ).fetchone()
        if (
            bad is None
            or duplicates is None
            or int(bad[0])
            or int(duplicates[0])
        ):
            _fail("failed candidate projection identity rows are invalid")
        identity_ambiguity = connection.execute(
            """
            SELECT count(*)
            FROM (
              SELECT dwd_security_id
              FROM read_parquet(?)
              GROUP BY dwd_security_id
              HAVING count(DISTINCT dwd_company_id) <> 1
                  OR count(DISTINCT listing_code) <> 1
                  OR count(DISTINCT venue_id) <> 1
            )
            """,
            [str(path)],
        ).fetchone()
        if identity_ambiguity is None or int(identity_ambiguity[0]):
            _fail("failed candidate projection has ambiguous security identity bindings")
        totals = connection.execute(
            """
            SELECT count(*) AS row_count,
                   count(DISTINCT dwd_security_id) AS security_count,
                   min(session_date) AS first_session,
                   max(session_date) AS last_session,
                   count(DISTINCT dwd_security_id) FILTER (
                     WHERE session_date BETWEEN ? AND ?
                   ) AS decision_security_count
            FROM read_parquet(?)
            """,
            [decision_start, decision_end, str(path)],
        ).fetchone()
        rows = connection.execute(
            """
            SELECT dwd_security_id,
                   min(dwd_company_id) AS dwd_company_id,
                   min(listing_code) AS listing_code,
                   min(venue_id) AS venue_id,
                   min(session_date) AS first_session,
                   max(session_date) AS last_session,
                   max(
                     CASE
                       WHEN session_date BETWEEN ? AND ? THEN 1
                       ELSE 0
                     END
                   ) AS in_decision_scope
            FROM read_parquet(?)
            GROUP BY dwd_security_id
            ORDER BY dwd_security_id
            """,
            [decision_start, decision_end, str(path)],
        ).fetchall()
    except duckdb.Error as exc:
        _source_unavailable(
            "failed candidate projection cannot be read by DuckDB",
            cause=exc,
        )
    finally:
        connection.close()

    if totals is None or totals[2] is None or totals[3] is None:
        _fail("failed candidate projection inventory is empty")
    identities = [
        {
            "security_id": str(security_id),
            "company_id": str(company_id),
            "listing_code": str(listing_code),
            "venue_id": str(venue_id),
            "first_session": first_session.isoformat(),
            "last_session": last_session.isoformat(),
            "in_decision_scope": bool(in_decision_scope),
        }
        for (
            security_id,
            company_id,
            listing_code,
            venue_id,
            first_session,
            last_session,
            in_decision_scope,
        ) in rows
    ]
    return {
        "status": "observed_after_failure",
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "claim_execution_time_bytes_reproduced": False,
        "row_count": int(totals[0]),
        "security_count": int(totals[1]),
        "decision_security_count": int(totals[4]),
        "first_session": totals[2].isoformat(),
        "last_session": totals[3].isoformat(),
    }, identities


def _p4_reference(
    run_root: Path,
    binding: dict[str, Any],
    *,
    json_limit: int,
) -> tuple[
    dict[str, Any],
    dict[str, tuple[str, str, str]],
    dict[tuple[str, str], list[str]],
]:
    expected_manifest = str(binding["event_manifest_sha256"])
    if _SHA256_RE.fullmatch(expected_manifest) is None:
        _fail("P4 event manifest binding is invalid")

    receipts: list[dict[str, Any]] = []
    raw_mapping: dict[str, Any] | None = None
    first_mapping_sha: str | None = None
    first_source_sha: str | None = None

    for run_id in binding["run_ids"]:
        event_root = (run_root / "runs" / str(run_id) / "event-library").resolve()
        event_manifest_path = event_root / "manifest.json"
        try:
            event_manifest = _object(
                event_manifest_path,
                root=event_root,
                limit=json_limit,
                label="P4 event manifest",
            )
            event_manifest_sha = _sha256(event_manifest_path)
        except (ExpertEvaluationError, OSError) as exc:
            _source_unavailable(
                f"P4 event reference {run_id} is unavailable",
                cause=exc,
            )
        try:
            ready = (event_root / "READY").read_text(encoding="ascii").strip()
        except OSError as exc:
            _source_unavailable(
                f"P4 event READY for {run_id} is unavailable",
                cause=exc,
            )
        if (
            event_manifest_sha != expected_manifest
            or ready != event_manifest_sha
            or not isinstance(event_manifest.get("adm"), dict)
        ):
            _fail("P4 event identity differs from the frozen reference")

        adm_root = event_root / "adm"
        adm_manifest_path = adm_root / "adm.json"
        try:
            adm_manifest = _object(
                adm_manifest_path,
                root=adm_root,
                limit=json_limit,
                label="P4 ADM manifest",
            )
            adm_manifest_sha = _sha256(adm_manifest_path)
        except (ExpertEvaluationError, OSError) as exc:
            _source_unavailable(
                f"P4 ADM reference {run_id} is unavailable",
                cause=exc,
            )
        if adm_manifest_sha != event_manifest["adm"].get("manifest_sha256"):
            _fail("P4 ADM manifest differs from the event binding")

        industry = adm_manifest.get("static_industry_projection")
        source = adm_manifest.get("static_industry_source_receipt")
        adm_policy = adm_manifest.get("adm_policy")
        if (
            not isinstance(industry, dict)
            or not isinstance(source, dict)
            or not isinstance(adm_policy, dict)
        ):
            _fail("P4 industry provenance binding is incomplete")

        mapping_path = _relative_child(
            adm_root,
            industry.get("path"),
            "P4 static industry mapping",
        )
        source_path = _relative_child(
            adm_root,
            source.get("path"),
            "P4 industry source receipt",
        )
        try:
            mapping_sha = _sha256(mapping_path)
            source_sha = _sha256(source_path)
        except OSError as exc:
            _source_unavailable(
                f"P4 industry assets for {run_id} are unavailable",
                cause=exc,
            )
        if mapping_sha != industry.get("sha256") or source_sha != source.get("sha256"):
            _fail("P4 industry asset checksum differs from its sealed ADM")

        try:
            mapping = load_static_industry_mapping(
                mapping_path,
                expected_version=str(industry["mapping_version"]),
            )
            source_receipt = _object(
                source_path,
                root=adm_root,
                limit=json_limit,
                label="P4 industry source receipt",
            )
        except (IndustryMappingError, ExpertEvaluationError, KeyError):
            _fail("P4 industry reference cannot be independently verified")

        historical_reference = adm_policy.get("historical_industry_reference")
        supplement = source_receipt.get("historical_supplement")
        source_receipt_digest = source_receipt.get("content_digest")
        source_receipt_payload = {
            key: value
            for key, value in source_receipt.items()
            if key != "content_digest"
        }
        supplement_digest = (
            supplement.get("content_digest")
            if isinstance(supplement, dict)
            else None
        )
        supplement_payload = (
            {
                key: value
                for key, value in supplement.items()
                if key != "content_digest"
            }
            if isinstance(supplement, dict)
            else {}
        )
        dwd_base = adm_manifest.get("dwd_base")
        if (
            source_receipt.get("schema_version")
            != "quanthus.p4-adm-static-industry-receipt.v1"
            or not isinstance(historical_reference, dict)
            or not isinstance(supplement, dict)
            or not isinstance(dwd_base, dict)
            or not isinstance(source_receipt_digest, str)
            or source_receipt_digest != digest_value(source_receipt_payload)
            or not isinstance(supplement_digest, str)
            or supplement_digest != digest_value(supplement_payload)
            or supplement_digest
            != historical_reference.get("source_content_digest")
            or mapping.source.source_revision != source_receipt_digest
            or source_receipt.get("dwd_base_package_id")
            != dwd_base.get("package_id")
        ):
            _fail("P4 historical industry provenance differs from its sealed policy")
        dwd_membership_count = source_receipt.get("dwd_membership_count")
        supplement_count = supplement.get("selected_security_count")
        if (
            not isinstance(dwd_membership_count, int)
            or isinstance(dwd_membership_count, bool)
            or not isinstance(supplement_count, int)
            or isinstance(supplement_count, bool)
            or dwd_membership_count + supplement_count != len(mapping.memberships)
        ):
            _fail("P4 industry source counts do not close to the sealed mapping")

        if first_mapping_sha is not None and mapping_sha != first_mapping_sha:
            _fail("accepted P4 A/B mappings differ")
        if first_source_sha is not None and source_sha != first_source_sha:
            _fail("accepted P4 A/B industry source receipts differ")
        first_mapping_sha = mapping_sha
        first_source_sha = source_sha

        if raw_mapping is None:
            raw_mapping = _object(
                mapping_path,
                root=adm_root,
                limit=int(binding["maximum_mapping_bytes"]),
                label="P4 static industry mapping",
            )
        receipts.append(
            {
                "run_id": str(run_id),
                "event_manifest_sha256": event_manifest_sha,
                "adm_manifest_sha256": adm_manifest_sha,
                "mapping_sha256": mapping_sha,
                "mapping_artifact_digest": mapping.artifact_digest,
                "membership_count": len(mapping.memberships),
                "source_receipt_sha256": source_sha,
                "historical_source_content_digest": supplement["content_digest"],
                "historical_supplement_security_count": supplement_count,
            }
        )

    if raw_mapping is None:
        _source_unavailable("P4 sealed mapping is unavailable")

    exact: dict[str, tuple[str, str, str]] = {}
    by_code: dict[tuple[str, str], list[str]] = {}
    memberships = raw_mapping.get("memberships")
    if not isinstance(memberships, list):
        _fail("P4 static industry mapping memberships are invalid")
    for row in memberships:
        if not isinstance(row, dict):
            _fail("P4 static industry membership is invalid")
        security_id = str(row.get("security_id") or "")
        stock_code = str(row.get("stock_code") or "")
        venue_id = str(row.get("exchange") or "")
        industry_id = str(row.get("level1_industry_id") or "")
        if not all((security_id, stock_code, venue_id, industry_id)):
            _fail("P4 static industry membership is incomplete")
        if security_id in exact:
            _fail("P4 static industry mapping contains duplicate security identities")
        exact[security_id] = (industry_id, stock_code, venue_id)
        by_code.setdefault((stock_code, venue_id), []).append(security_id)

    return {"runs": receipts}, exact, by_code


def _dwd_reference(
    base_root: Path,
    identities: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ExpertEvaluationError("dependency_missing", "duckdb") from exc

    try:
        membership_expression = _read_parquet_expression(
            _table_paths(base_root, "dwd_company_industry_membership")
        )
        node_expression = _read_parquet_expression(
            _table_paths(base_root, "dwd_industry_node")
        )
    except PhysicalEventScanError as exc:
        _source_unavailable(
            "DWD static industry source tables are unavailable",
            cause=exc,
        )

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE candidate_identity(security_id VARCHAR, company_id VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO candidate_identity VALUES (?, ?)",
            [
                (identity["security_id"], identity["company_id"])
                for identity in identities
            ],
        )
        rows = connection.execute(
            f"""
            WITH membership AS (
              SELECT company_id, industry_node_id
              FROM {membership_expression}
              WHERE knowledge_to IS NULL
                AND membership_type='PRIMARY_CLASSIFICATION'
            ),
            nodes AS (
              SELECT industry_node_id, parent_node_id, node_level
              FROM {node_expression}
              WHERE knowledge_to IS NULL
            )
            SELECT candidate.security_id,
                   count(DISTINCT membership.industry_node_id) AS membership_count,
                   count(DISTINCT node.industry_node_id) AS node_count,
                   count(
                     DISTINCT CASE
                       WHEN node.node_level=1 THEN node.industry_node_id
                       WHEN node.node_level=2 AND parent.node_level=1
                         THEN parent.industry_node_id
                     END
                   ) AS level1_count,
                   min(
                     CASE
                       WHEN node.node_level=1 THEN node.industry_node_id
                       WHEN node.node_level=2 AND parent.node_level=1
                         THEN parent.industry_node_id
                     END
                   ) AS level1_industry_id
            FROM candidate_identity candidate
            LEFT JOIN membership
              ON membership.company_id=candidate.company_id
            LEFT JOIN nodes node
              ON node.industry_node_id=membership.industry_node_id
            LEFT JOIN nodes parent
              ON parent.industry_node_id=node.parent_node_id
            GROUP BY candidate.security_id
            ORDER BY candidate.security_id
            """
        ).fetchall()
    except duckdb.Error as exc:
        _source_unavailable(
            "DWD industry coverage query failed",
            cause=exc,
        )
    finally:
        connection.close()

    mapping: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for security_id, membership_count, node_count, level1_count, industry_id in rows:
        security = str(security_id)
        if int(level1_count) == 1 and industry_id is not None:
            mapping[security] = str(industry_id)
            reason = "covered"
        elif int(level1_count) > 1:
            reason = "ambiguous_level1_membership"
        elif int(membership_count) == 0:
            reason = "no_company_industry_membership"
        elif int(node_count) == 0:
            reason = "industry_node_missing"
        else:
            reason = "level1_parent_unresolved"
        reasons[security] = reason
    return mapping, reasons


def _legacy_reference(runtime: Any) -> tuple[dict[str, str], dict[str, Any]]:
    try:
        mapping = load_static_industry_mapping(
            runtime.static_industry_mapping,
            expected_version="static-level1-concentration-research-v1",
        )
        mapping_sha = _sha256(runtime.static_industry_mapping)
    except (IndustryMappingError, OSError) as exc:
        _source_unavailable(
            "legacy P3 static industry mapping is unavailable or invalid",
            cause=exc,
        )
    if mapping_sha != runtime.static_industry_mapping_sha256:
        _fail("legacy P3 static industry mapping checksum differs")
    return (
        {
            security_id: membership.level1_industry_id
            for security_id, membership in mapping.by_security_id.items()
        },
        {
            "path": str(runtime.static_industry_mapping),
            "sha256": mapping_sha,
            "membership_count": len(mapping.memberships),
            "artifact_digest": mapping.artifact_digest,
        },
    )


def _coverage(
    identities: list[dict[str, Any]],
    *,
    legacy: dict[str, str],
    dwd: dict[str, str],
    dwd_reasons: dict[str, str],
    p4: dict[str, tuple[str, str, str]],
    p4_by_code: dict[tuple[str, str], list[str]],
    sample_limit: int,
) -> dict[str, Any]:
    all_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    unresolved: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    code_only_matches = 0

    for identity in identities:
        security_id = str(identity["security_id"])
        p4_value = p4.get(security_id)
        dwd_value = dwd.get(security_id)
        if dwd_value is not None:
            status = "dwd_native"
            resolved_value = dwd_value
        elif p4_value is not None:
            status = "p4_sealed_reference"
            resolved_value = p4_value[0]
        else:
            status = dwd_reasons.get(security_id, "unclassified")
            resolved_value = None

        all_counts[status] += 1
        if identity["in_decision_scope"]:
            decision_counts[status] += 1

        if (
            dwd_value is not None
            and p4_value is not None
            and dwd_value != p4_value[0]
        ):
            conflicts.append(
                {
                    **identity,
                    "dwd_level1_industry_id": dwd_value,
                    "p4_level1_industry_id": p4_value[0],
                }
            )

        if resolved_value is None:
            code_matches = p4_by_code.get(
                (str(identity["listing_code"]), str(identity["venue_id"])),
                [],
            )
            if len(code_matches) == 1 and code_matches[0] != security_id:
                code_only_matches += 1
            unresolved.append(
                {
                    **identity,
                    "dwd_reason": dwd_reasons.get(security_id),
                    "legacy_p3_exact_identity": security_id in legacy,
                }
            )

    return {
        "all_security_status_counts": dict(sorted(all_counts.items())),
        "decision_security_status_counts": dict(sorted(decision_counts.items())),
        "legacy_p3_exact_security_count": sum(
            identity["security_id"] in legacy for identity in identities
        ),
        "remaining_unresolved_security_count": len(unresolved),
        "dwd_p4_conflict_count": len(conflicts),
        "code_venue_only_reference_match_count": code_only_matches,
        "remaining_unresolved_samples": unresolved[:sample_limit],
        "conflict_samples": conflicts[:sample_limit],
    }


def _seal(
    *,
    output: Path,
    code_revision: str,
    core: dict[str, Any],
    output_limit: int,
) -> dict[str, Any]:
    core.update(
        {
            "claim_failed_job_artifact_reproduced": False,
            "strategy_or_economic_results_read": False,
            "target_outcome_labels_read": False,
            "p6_mutation": False,
            "daily_current_mutation": False,
        }
    )
    summary = {
        "schema_version": "quanthus.p3-strat-003-industry-inspection-summary.v1",
        "work_item": "P3-STRAT-003-B-R1",
        "code_revision": code_revision,
        "request_digest": REQUEST_DIGEST,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "inspection": core,
    }
    summary_data = canonical_json_bytes(summary) + b"\n"
    if len(summary_data) > output_limit:
        _fail("industry inspection summary exceeds output limit")
    payload = {
        "schema_version": ARTIFACT_SCHEMA,
        "work_item": "P3-STRAT-003-B-R1",
        "code_revision": code_revision,
        "request_digest": REQUEST_DIGEST,
        "disposition": core["disposition"],
        "row_count": 0,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "candidate_event_count": 0,
        "execution_authorized": False,
        "strategy_result_produced": False,
        "outcome_labels_produced": False,
        "p6_mutation": False,
        "daily_current_mutation": False,
        "b_acceptance": False,
        "inspection": core,
        "files": {
            "summary": {
                "path": "summary.json",
                "size_bytes": len(summary_data),
                "sha256": "sha256:" + hashlib.sha256(summary_data).hexdigest(),
            }
        },
    }
    manifest = {**payload, "result_digest": digest_value(payload)}
    manifest_data = canonical_json_bytes(manifest) + b"\n"
    ready = (
        "sha256:" + hashlib.sha256(manifest_data).hexdigest() + "\n"
    ).encode("ascii")
    if len(summary_data) + len(manifest_data) + len(ready) > output_limit:
        _fail("complete industry inspection artifact exceeds output limit")
    output.mkdir(parents=True)
    (output / "summary.json").write_bytes(summary_data)
    (output / "manifest.json").write_bytes(manifest_data)
    (output / "READY").write_bytes(ready)
    return verify_industry_inspection_artifact(
        output,
        code_revision=code_revision,
        request_digest=REQUEST_DIGEST,
    )


def build_industry_inspection(
    *,
    request_path: Path,
    output: Path,
    code_revision: str,
) -> dict[str, Any]:
    if _GIT_RE.fullmatch(code_revision) is None:
        _fail("code revision is invalid")
    project = find_project_root(request_path.resolve())
    raw = _object(
        request_path.resolve(),
        root=project,
        limit=262144,
        label="industry inspection request",
    )
    if (
        raw.get("schema_version") != REQUEST_SCHEMA
        or raw.get("work_item") != "P3-STRAT-003-B-R1"
        or digest_value(raw) != REQUEST_DIGEST
    ):
        _fail("industry inspection request differs from the frozen contract")

    qualification_path = (project / str(raw["qualification"]["path"])).resolve()
    try:
        qualification_sha = _sha256(qualification_path)
    except OSError:
        _fail("bound B-v2 request is unavailable")
    if qualification_sha != raw["qualification"]["file_sha256"]:
        _fail("bound B-v2 request differs")
    qualification = _request(qualification_path)
    runtime = load_prospective_runtime_config(
        project / str(qualification["source_runtime"]["path"]),
        root=project,
    )

    run_root = _run_root()
    output = output.resolve()
    if not output.is_relative_to((run_root / ".staging").resolve()):
        _fail("industry inspection output must stay inside Event Worker staging")

    decision_start = date.fromisoformat(raw["scope"]["decision_start"])
    decision_end = date.fromisoformat(raw["scope"]["decision_end"])
    candidate, identities = _candidate_inventory(
        run_root,
        raw["failed_job"],
        decision_start=decision_start,
        decision_end=decision_end,
    )
    if not identities:
        core = {
            "disposition": UNAVAILABLE,
            "failed_job": raw["failed_job"],
            "candidate_projection": candidate,
        }
        return _seal(
            output=output,
            code_revision=code_revision,
            core=core,
            output_limit=int(raw["limits"]["maximum_output_bytes"]),
        )

    try:
        legacy, legacy_receipt = _legacy_reference(runtime)
        p4_receipt, p4_mapping, p4_by_code = _p4_reference(
            run_root,
            raw["p4_reference"],
            json_limit=int(raw["limits"]["maximum_json_bytes"]),
        )
        dwd_mapping, dwd_reasons = _dwd_reference(
            runtime.historical_base_root.resolve(),
            identities,
        )
    except ExpertEvaluationError as exc:
        core = {
            "disposition": UNAVAILABLE,
            "failed_job": raw["failed_job"],
            "candidate_projection": candidate,
            "industry_sources": {
                "status": "unavailable_or_invalid",
                "reason_code": exc.reason_code,
                "detail": str(exc)[:300],
            },
        }
    else:
        core = {
            "disposition": COMPLETE,
            "failed_job": raw["failed_job"],
            "candidate_projection": candidate,
            "legacy_p3_mapping": legacy_receipt,
            "dwd_reason_counts": dict(sorted(Counter(dwd_reasons.values()).items())),
            "p4_reference": p4_receipt,
            "coverage": _coverage(
                identities,
                legacy=legacy,
                dwd=dwd_mapping,
                dwd_reasons=dwd_reasons,
                p4=p4_mapping,
                p4_by_code=p4_by_code,
                sample_limit=int(raw["limits"]["maximum_sample_rows_per_class"]),
            ),
        }

    return _seal(
        output=output,
        code_revision=code_revision,
        core=core,
        output_limit=int(raw["limits"]["maximum_output_bytes"]),
    )


def verify_industry_inspection_artifact(
    root: Path,
    *,
    code_revision: str,
    request_digest: str,
) -> dict[str, Any]:
    root = root.resolve()
    manifest = _object(
        root / "manifest.json",
        root=root,
        limit=262144,
        label="industry inspection manifest",
    )
    summary = _object(
        root / "summary.json",
        root=root,
        limit=262144,
        label="industry inspection summary",
    )
    ready_path = root / "READY"
    if ready_path.is_symlink() or not ready_path.resolve().is_relative_to(root):
        _fail("industry inspection READY escapes artifact root")
    try:
        ready = ready_path.read_text(encoding="ascii").strip()
        manifest_sha = _sha256(root / "manifest.json")
    except OSError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_industry_inspection_unreadable",
            "industry inspection READY",
        ) from exc

    payload = {
        key: value for key, value in manifest.items() if key != "result_digest"
    }
    expected_file = {
        "path": "summary.json",
        "size_bytes": (root / "summary.json").stat().st_size,
        "sha256": _sha256(root / "summary.json"),
    }
    false_flags = (
        "execution_authorized",
        "strategy_result_produced",
        "outcome_labels_produced",
        "p6_mutation",
        "daily_current_mutation",
        "b_acceptance",
    )
    zero_counts = (
        "row_count",
        "p2_probe_count",
        "p2_fill_count",
        "candidate_event_count",
    )
    if (
        ready != manifest_sha
        or manifest.get("schema_version") != ARTIFACT_SCHEMA
        or manifest.get("work_item") != "P3-STRAT-003-B-R1"
        or manifest.get("code_revision") != code_revision
        or request_digest != REQUEST_DIGEST
        or manifest.get("request_digest") != request_digest
        or manifest.get("disposition") not in {COMPLETE, UNAVAILABLE}
        or manifest.get("result_digest") != digest_value(payload)
        or manifest.get("files") != {"summary": expected_file}
        or manifest.get("inspection") != summary.get("inspection")
        or manifest.get("disposition")
        != manifest.get("inspection", {}).get("disposition")
        or any(manifest.get(field) is not False for field in false_flags)
        or any(manifest.get(field) != 0 for field in zero_counts)
    ):
        _fail("sealed industry inspection artifact boundary differs")

    result: dict[str, Any] = {
        "artifact_ref": str(root),
        "manifest_sha256": manifest_sha,
        "result_digest": manifest["result_digest"],
        "request_digest": request_digest,
        "disposition": manifest["disposition"],
        "row_count": 0,
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "candidate_event_count": 0,
        "execution_authorized": False,
        "strategy_result_produced": False,
        "outcome_labels_produced": False,
        "p6_mutation": False,
        "daily_current_mutation": False,
        "b_acceptance": False,
    }
    coverage = manifest["inspection"].get("coverage")
    if isinstance(coverage, dict):
        result.update(
            {
                "remaining_unresolved_security_count": int(
                    coverage["remaining_unresolved_security_count"]
                ),
                "dwd_p4_conflict_count": int(
                    coverage["dwd_p4_conflict_count"]
                ),
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect P3-STRAT-003-B static-industry input coverage"
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    signal.alarm(120)
    result = build_industry_inspection(
        request_path=args.request,
        output=args.output,
        code_revision=args.code_revision,
    )
    signal.alarm(0)
    print(
        json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
