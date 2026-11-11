"""Build and verify immutable corrections bound to the DWD v2.1 Base."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any


class DwdCorrectionError(RuntimeError):
    """Raised when a DWD correction cannot be proved against its parent."""


PARENT_PACKAGE_ID = "dwd-base-202607-v2.1"
PARENT_MANIFEST_SHA256 = "sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b"
PARENT_CONTENT_DIGEST = "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
NORMALIZED_REVISION = "corporate_action_replay_2012_20260730_v3"
NORMALIZED_POLICY = "p1-corporate-action-normalization-20260730-v3"
NORMALIZED_LEDGER = "p1-corporate-action-exceptions-20260730-v2"
CORRECTION_SCHEMA_VERSION = 2
_REVISION_MEMBERS = (
    "corporate_actions_normalized.parquet",
    "corporate_action_exceptions.parquet",
    "audit_report.json",
    "exception_changes.json",
)
_REVISION_METADATA = (
    "manifest.json",
    "audit_report.json",
    "exception_changes.json",
    "CHECKSUMS.sha256",
)
_SOURCE_CLOSURE = {
    "contract": "normalization_revision_closure_v1",
    "root": "payload/replay",
    "manifest_path": "payload/replay/manifest.json",
    "checksum_ledger_path": "payload/replay/CHECKSUMS.sha256",
    "members": {name: f"payload/replay/{name}" for name in _REVISION_MEMBERS},
    "upstream_raw_sources_retained": False,
}


@dataclass(frozen=True)
class DwdCorrectionBundle:
    root: Path
    correction_id: str
    manifest_sha256: str
    content_digest: str
    row_counts: dict[str, int]
    source_closure_status: str = "legacy_manifest_unretained"

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "correction_id": self.correction_id,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "row_counts": self.row_counts,
            "source_closure_status": self.source_closure_status,
        }


def _dependencies() -> tuple[Any, Any]:
    try:
        import duckdb
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise DwdCorrectionError("duckdb and pyarrow are required for DWD correction") from exc
    return duckdb, pq


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DwdCorrectionError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdCorrectionError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise DwdCorrectionError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _safe_relative(value: str) -> Path:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or path.as_posix() != value
        or "\\" in value
    ):
        raise DwdCorrectionError(f"unsafe relative path: {value!r}")
    return Path(*path.parts)


def _parent_identity(parent: Path) -> dict[str, str]:
    manifest_path = parent / "manifest.json"
    manifest = _read_json(manifest_path)
    identity = {
        "package_id": PARENT_PACKAGE_ID,
        "manifest_sha256": PARENT_MANIFEST_SHA256,
        "content_digest": PARENT_CONTENT_DIGEST,
    }
    if (
        manifest.get("package_id") != PARENT_PACKAGE_ID
        or manifest.get("content_digest") != PARENT_CONTENT_DIGEST
        or _sha256(manifest_path) != PARENT_MANIFEST_SHA256
        or not (parent / "READY").is_file()
        or (parent / "READY").read_text(encoding="ascii").strip() != PARENT_MANIFEST_SHA256
    ):
        raise DwdCorrectionError("DWD correction parent identity differs")
    return identity


def _physical_file(root: Path, relative: str) -> Path:
    path = root / _safe_relative(relative)
    current = path
    while current != root.parent:
        if current.is_symlink():
            raise DwdCorrectionError(f"source or correction member is a symlink: {relative}")
        current = current.parent
    if not path.is_file():
        raise DwdCorrectionError(f"source or correction member is missing: {relative}")
    return path


def _revision_identity(revision: Path) -> dict[str, str]:
    from quanthus.corporate_action_revision import (
        CorporateActionRevisionError,
        check_corporate_action_revision,
    )

    for name in (*_REVISION_MEMBERS, "manifest.json", "CHECKSUMS.sha256"):
        _physical_file(revision, name)
    if {path.name for path in revision.iterdir()} != {
        *_REVISION_MEMBERS,
        "manifest.json",
        "CHECKSUMS.sha256",
    }:
        raise DwdCorrectionError("normalization revision physical inventory is not exact")
    manifest_path = revision / "manifest.json"
    manifest_sha = _sha256(manifest_path)
    ledger_sha = _sha256(revision / "CHECKSUMS.sha256")
    manifest = _read_json(manifest_path)
    policy = manifest.get("policy")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "corporate_action_replay_revision"
        or manifest.get("revision") != NORMALIZED_REVISION
        or manifest.get("ready_for_snapshot") is not True
        or manifest.get("execution_authorized") is not False
        or not isinstance(policy, dict)
        or policy.get("normalization_policy_version") != NORMALIZED_POLICY
        or policy.get("exception_ledger_version") != NORMALIZED_LEDGER
        or policy.get("normalization_version") != "2.0.0"
    ):
        raise DwdCorrectionError("corporate-action normalization revision differs")
    scope = manifest.get("scope")
    if not isinstance(scope, dict):
        raise DwdCorrectionError("normalization revision scope is missing")
    try:
        start, end = date.fromisoformat(scope["start"]), date.fromisoformat(scope["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DwdCorrectionError("normalization revision scope is invalid") from exc
    if start > end:
        raise DwdCorrectionError("normalization revision scope is reversed")
    try:
        checked = check_corporate_action_revision(revision, expected_revision=NORMALIZED_REVISION)
    except (CorporateActionRevisionError, OSError, UnicodeError) as exc:
        raise DwdCorrectionError(f"normalization revision verification failed: {exc}") from exc
    _verify_revision_lineage(revision, manifest, checked.normalized_count, checked.exception_count)
    if (
        _sha256(manifest_path) != manifest_sha
        or _sha256(revision / "CHECKSUMS.sha256") != ledger_sha
    ):
        raise DwdCorrectionError("normalization metadata changed during verification")
    return {
        "revision": NORMALIZED_REVISION,
        "manifest_sha256": manifest_sha,
        "checksum_ledger_sha256": ledger_sha,
        "policy_version": NORMALIZED_POLICY,
    }


def _verify_revision_lineage(
    revision: Path, manifest: dict[str, Any], normalized_count: int, exception_count: int
) -> None:
    """Bind retained reports, policy and replay members, not unavailable raw sources."""

    source = manifest.get("source")
    if not isinstance(source, dict):
        raise DwdCorrectionError("normalization revision source is missing")
    for name in ("snapshot_id", "authority_source_revision"):
        if not isinstance(source.get(name), str) or not source[name].strip():
            raise DwdCorrectionError(f"normalization revision source identity is missing: {name}")
    digests = source.get("snapshot_dataset_digests")
    if not isinstance(digests, dict) or not digests:
        raise DwdCorrectionError("normalization snapshot source digests are missing")
    for value in [
        *digests.values(),
        source.get("corporate_action_source_digest"),
        source.get("authority_source_digest"),
    ]:
        if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise DwdCorrectionError("normalization source digest is invalid")
    report = _read_json(revision / "audit_report.json")
    changes = _read_json(revision / "exception_changes.json")
    lineage = manifest["lineage"]
    ledger = report.get("exception_ledger")
    normalization = report.get("normalization")
    if not isinstance(ledger, dict) or not isinstance(normalization, dict):
        raise DwdCorrectionError("normalization report or exception ledger is missing")
    if (
        _canonical_digest(report) != lineage["normalization_report_digest"]
        or _canonical_digest(ledger) != lineage["exception_ledger_digest"]
        or changes.get("current_exception_ledger_digest") != lineage["exception_ledger_digest"]
        or report.get("ready_for_snapshot") is not True
        or any(
            item.get("execution_authorized") is not False
            for item in (report, normalization, changes)
        )
        or report.get("scope_start") != manifest["scope"]["start"]
        or report.get("scope_end") != manifest["scope"]["end"]
        or report.get("source_row_count") != manifest["counts"].get("source_rows")
        or report.get("exception_count") != exception_count
        or ledger.get("entry_count") != exception_count
        or normalization.get("normalized_count") != normalized_count
        or normalization.get("policy_version") != NORMALIZED_POLICY
        or normalization.get("normalization_version") != manifest["policy"]["normalization_version"]
        or ledger.get("version") != NORMALIZED_LEDGER
        or ledger.get("policy_version") != NORMALIZED_POLICY
    ):
        raise DwdCorrectionError("normalization report, policy or exception lineage differs")
    for source_name, report_name in (
        ("snapshot_id", "snapshot_id"),
        ("snapshot_dataset_digests", "snapshot_dataset_digests"),
        ("corporate_action_source_digest", "action_source_digest"),
        ("authority_source_revision", "authority_source_revision"),
        ("authority_source_digest", "authority_source_digest"),
    ):
        if source[source_name] != report.get(report_name):
            raise DwdCorrectionError(f"normalization report source differs: {source_name}")
    for name in ("action_source_digest", "authority_source_digest"):
        if ledger.get(name) != report[name]:
            raise DwdCorrectionError(f"normalization exception source differs: {name}")
    common = {
        "normalization_policy_version": NORMALIZED_POLICY,
        "normalization_report_digest": lineage["normalization_report_digest"],
        "corporate_action_source_digest": source["corporate_action_source_digest"],
        "authority_source_digest": source["authority_source_digest"],
        "exception_ledger_digest": lineage["exception_ledger_digest"],
        "exception_ledger_version": NORMALIZED_LEDGER,
    }
    _, pq = _dependencies()
    for name in _REVISION_MEMBERS[:2]:
        dataset = name.removesuffix(".parquet")
        binding = manifest["snapshot_source_revisions"][dataset]
        if any(binding.get(key) != value for key, value in common.items()):
            raise DwdCorrectionError(f"normalization snapshot lineage differs: {dataset}")
        columns = {"execution_authorized": False, "normalization_policy_version": NORMALIZED_POLICY}
        if dataset == "corporate_action_exceptions":
            if binding.get("replay_disposition") != "security_fail_closed":
                raise DwdCorrectionError("normalization exception disposition differs")
            columns.update(
                exception_ledger_version=NORMALIZED_LEDGER,
                replay_disposition="security_fail_closed",
            )
        else:
            columns.update(
                normalization_version=manifest["policy"]["normalization_version"],
                source_snapshot_id=source["snapshot_id"],
            )
        try:
            parquet = pq.ParquetFile(revision / name)
            if not set(columns) <= set(parquet.schema_arrow.names):
                raise DwdCorrectionError(f"normalization member authority columns missing: {name}")
            for batch in parquet.iter_batches(columns=list(columns)):
                for row in batch.to_pylist():
                    if row["execution_authorized"] is not False or any(
                        row[key] != value for key, value in columns.items()
                    ):
                        raise DwdCorrectionError(
                            f"normalization member policy or authority differs: {name}"
                        )
        except DwdCorrectionError:
            raise
        except Exception as exc:
            raise DwdCorrectionError(f"cannot verify normalization member: {name}") from exc


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _copy_query(connection: Any, query: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(
        f"COPY ({query}) TO '{_sql_path(target)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    return int(
        connection.execute("SELECT count(*) FROM read_parquet(?)", [str(target)]).fetchone()[0]
    )


def _base_paths(parent: Path) -> dict[str, str]:
    root = _sql_path(parent)
    return {
        "state": f"{root}/payload/tradability_state/dwd_tradability_state/**/*.parquet",
        "bar": f"{root}/payload/security_market_observation/dwd_security_bar/**/*.parquet",
        "action": f"{root}/payload/disclosure_company_event/dwd_corporate_action/**/*.parquet",
        "term": f"{root}/payload/disclosure_company_event/dwd_corporate_action_term/**/*.parquet",
        "disclosure": f"{root}/payload/disclosure_company_event/dwd_disclosure/**/*.parquet",
        "subject": f"{root}/payload/disclosure_company_event/dwd_disclosure_subject/**/*.parquet",
    }


def _build_payload(
    *,
    parent: Path,
    revision: Path,
    staging: Path,
    correction_id: str,
    built_at: str,
) -> tuple[dict[str, int], dict[str, Any]]:
    duckdb, _ = _dependencies()
    connection = duckdb.connect()
    paths = _base_paths(parent)
    payload = staging / "payload"
    try:
        connection.execute(
            f"""
            CREATE VIEW base_action AS
            SELECT * FROM read_parquet('{paths["action"]}', hive_partitioning=true);
            CREATE VIEW base_term AS
            SELECT * FROM read_parquet('{paths["term"]}', hive_partitioning=true);
            CREATE VIEW action_fact AS
            SELECT a.corporate_action_id,a.security_id,
                   CAST(a.effective_at AS DATE) AS effective_date,
                   CAST(a.announced_at AS DATE) AS announced_date,
                   a.record_source,t.corporate_action_term_id,t.term_type,
                   coalesce(t.amount_per_share,
                     t.ratio_numerator/nullif(t.ratio_denominator,0))::DECIMAL(38,10)
                     AS term_value
            FROM base_action a JOIN base_term t USING (corporate_action_id);
            CREATE VIEW deleted_term AS
            SELECT DISTINCT d.corporate_action_term_id
            FROM action_fact d
            WHERE d.record_source='dividend_history' AND (
              d.term_value=0 OR EXISTS (
                SELECT 1 FROM action_fact a
                WHERE a.record_source='corporate_actions'
                  AND a.security_id=d.security_id
                  AND a.effective_date=d.effective_date
                  AND a.announced_date IS NOT DISTINCT FROM d.announced_date
                  AND a.term_type=d.term_type AND a.term_value=d.term_value
              )
            );
            CREATE VIEW deleted_action AS
            SELECT a.corporate_action_id
            FROM base_action a JOIN base_term t USING (corporate_action_id)
            WHERE a.record_source='dividend_history'
            GROUP BY a.corporate_action_id
            HAVING count(*)=count(*) FILTER (
              WHERE t.corporate_action_term_id IN (
                SELECT corporate_action_term_id FROM deleted_term
              )
            );
            """
        )
        row_counts: dict[str, int] = {}
        row_counts["dwd_tradability_state_replacement"] = _copy_query(
            connection,
            f"""
            SELECT 'dwd_v2_1_external_findings_correction' AS record_source,
                   s.source_record_id,'{correction_id}' AS source_revision,
                   '{PARENT_CONTENT_DIGEST}' AS source_artifact_sha256,
                   TIMESTAMPTZ '{built_at}' AS knowledge_from,
                   NULL::TIMESTAMPTZ AS knowledge_to,
                   s.observation_start,s.observation_end,s.listing_id,s.calendar_session_id,
                   false AS is_tradable,false AS can_buy,false AS can_sell,
                   'ZERO_ACTIVITY_OBSERVATION' AS reason_code,
                   NULL::DECIMAL(38,10) AS upper_price,
                   NULL::DECIMAL(38,10) AS lower_price,
                   'sha256:' || sha256(concat_ws(chr(31),s.input_revision_digest,
                     '{correction_id}',CAST(b.volume AS VARCHAR),CAST(b.turnover AS VARCHAR)))
                     AS input_revision_digest,
                   s.observation_start_month,s.observation_start_year
            FROM read_parquet('{paths["state"]}', hive_partitioning=true) s
            JOIN read_parquet('{paths["bar"]}', hive_partitioning=true) b
              USING (listing_id,calendar_session_id)
            WHERE s.knowledge_to IS NULL AND s.is_tradable
              AND b.knowledge_to IS NULL AND b.interval_size='1d'
              AND b.price_basis='UNADJUSTED'
              AND (b.volume IS NULL OR b.volume<=0 OR b.turnover IS NULL OR b.turnover<=0)
            ORDER BY s.observation_start,s.listing_id
            """,
            payload / "tradability_state/dwd_tradability_state_replacement.parquet",
        )
        row_counts["dwd_corporate_action_term_delete"] = _copy_query(
            connection,
            f"""
            SELECT 'dwd_corporate_action_term' AS table_name,
                   t.corporate_action_term_id AS record_id,
                   CASE WHEN f.term_value=0 THEN 'ZERO_VALUE_TERM'
                        ELSE 'SUPPLEMENTAL_EXACT_AUTHORITY_OVERLAP' END AS reason_code,
                   '{correction_id}' AS correction_id
            FROM base_term t JOIN action_fact f USING (corporate_action_term_id)
            WHERE t.corporate_action_term_id IN (SELECT corporate_action_term_id FROM deleted_term)
            ORDER BY record_id
            """,
            payload / "disclosure_company_event/dwd_corporate_action_term_delete.parquet",
        )
        row_counts["dwd_corporate_action_delete"] = _copy_query(
            connection,
            f"""
            SELECT 'dwd_corporate_action' AS table_name,corporate_action_id AS record_id,
                   'NO_RETAINED_ECONOMIC_TERM' AS reason_code,
                   '{correction_id}' AS correction_id
            FROM deleted_action ORDER BY record_id
            """,
            payload / "disclosure_company_event/dwd_corporate_action_delete.parquet",
        )
        row_counts["dwd_disclosure_subject_delete"] = _copy_query(
            connection,
            f"""
            SELECT 'dwd_disclosure_subject' AS table_name,s.disclosure_subject_id AS record_id,
                   'REMOVED_DUPLICATE_CORPORATE_ACTION' AS reason_code,
                   '{correction_id}' AS correction_id
            FROM read_parquet('{paths["subject"]}', hive_partitioning=true) s
            WHERE s.corporate_action_id IN (SELECT corporate_action_id FROM deleted_action)
            ORDER BY record_id
            """,
            payload / "disclosure_company_event/dwd_disclosure_subject_delete.parquet",
        )
        row_counts["dwd_disclosure_delete"] = _copy_query(
            connection,
            f"""
            SELECT 'dwd_disclosure' AS table_name,d.disclosure_id AS record_id,
                   'REMOVED_DUPLICATE_CORPORATE_ACTION' AS reason_code,
                   '{correction_id}' AS correction_id
            FROM read_parquet('{paths["disclosure"]}', hive_partitioning=true) d
            WHERE d.disclosure_id IN (
              SELECT s.disclosure_id
              FROM read_parquet('{paths["subject"]}', hive_partitioning=true) s
              WHERE s.corporate_action_id IN (SELECT corporate_action_id FROM deleted_action)
            ) ORDER BY record_id
            """,
            payload / "disclosure_company_event/dwd_disclosure_delete.parquet",
        )
        row_counts["dwd_corporate_action_term_replacement"] = _copy_query(
            connection,
            f"""
            SELECT t.* REPLACE (
              'dwd_v2_1_external_findings_correction' AS record_source,
              '{correction_id}' AS source_revision,
              '{PARENT_CONTENT_DIGEST}' AS source_artifact_sha256,
              TIMESTAMPTZ '{built_at}' AS knowledge_from,
              CAST(t.ratio_numerator/10 AS DECIMAL(38,10)) AS ratio_numerator
            )
            FROM base_term t JOIN base_action a USING (corporate_action_id)
            WHERE a.record_source='corporate_actions'
              AND t.term_type IN ('BONUS_SHARES_PER_SHARE','TRANSFER_SHARES_PER_SHARE')
              AND t.ratio_numerator<>0
            ORDER BY t.corporate_action_term_id
            """,
            payload / "disclosure_company_event/dwd_corporate_action_term_replacement.parquet",
        )
        replay_root = payload / "replay"
        replay_root.mkdir(parents=True, exist_ok=True)
        for name in (
            "corporate_actions_normalized.parquet",
            "corporate_action_exceptions.parquet",
        ):
            shutil.copyfile(revision / name, replay_root / name)
            row_counts[name.removesuffix(".parquet")] = int(
                connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(replay_root / name)]
                ).fetchone()[0]
            )
        exact_overlaps = int(
            connection.execute(
                """
                SELECT count(*) FROM action_fact d
                WHERE d.record_source='dividend_history' AND d.term_value<>0
                  AND EXISTS (
                    SELECT 1 FROM action_fact a
                    WHERE a.record_source='corporate_actions'
                      AND a.security_id=d.security_id
                      AND a.effective_date=d.effective_date
                      AND a.announced_date IS NOT DISTINCT FROM d.announced_date
                      AND a.term_type=d.term_type AND a.term_value=d.term_value
                  )
                """
            ).fetchone()[0]
        )
        value_conflicts = int(
            connection.execute(
                """
                SELECT count(*) FROM action_fact d
                WHERE d.record_source='dividend_history' AND d.term_value<>0
                  AND EXISTS (
                    SELECT 1 FROM action_fact a
                    WHERE a.record_source='corporate_actions'
                      AND a.security_id=d.security_id
                      AND a.effective_date=d.effective_date
                      AND a.announced_date IS NOT DISTINCT FROM d.announced_date
                      AND a.term_type=d.term_type
                  ) AND NOT EXISTS (
                    SELECT 1 FROM action_fact a
                    WHERE a.record_source='corporate_actions'
                      AND a.security_id=d.security_id
                      AND a.effective_date=d.effective_date
                      AND a.announced_date IS NOT DISTINCT FROM d.announced_date
                      AND a.term_type=d.term_type AND a.term_value=d.term_value
                  )
                """
            ).fetchone()[0]
        )
        if value_conflicts:
            raise DwdCorrectionError(
                f"corporate-action authority conflicts remain: {value_conflicts}"
            )
        audit = {
            "tradability_replacements": row_counts["dwd_tradability_state_replacement"],
            "corporate_action_exact_term_overlaps": exact_overlaps,
            "corporate_action_value_conflicts": value_conflicts,
            "corporate_action_zero_terms": int(
                connection.execute(
                    "SELECT count(*) FROM action_fact WHERE term_value=0"
                ).fetchone()[0]
            ),
            "corporate_action_fully_removed": row_counts["dwd_corporate_action_delete"],
            "corporate_action_share_terms_normalized": row_counts[
                "dwd_corporate_action_term_replacement"
            ],
        }
        return row_counts, audit
    finally:
        connection.close()


def _file_inventory(root: Path) -> list[dict[str, Any]]:
    _, pq = _dependencies()
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.relative_to(root).as_posix() in {"manifest.json", "READY"}:
            continue
        record: dict[str, Any] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        if path.suffix == ".parquet":
            record["row_count"] = pq.ParquetFile(path).metadata.num_rows
            record["format"] = "parquet"
        elif path.suffix == ".json":
            record["format"] = "json"
        else:
            record["format"] = "text"
        files.append(record)
    return files


def build_dwd_correction(
    *,
    parent: Path,
    normalization_revision: Path,
    output: Path,
    correction_id: str,
    built_at: str,
    code_revision: str,
) -> DwdCorrectionBundle:
    """Build one immutable Base-bound correction without mutating the parent."""

    parent = parent.resolve()
    if normalization_revision.is_symlink():
        raise DwdCorrectionError("normalization revision must be a physical directory")
    normalization_revision = normalization_revision.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite DWD correction: {output}")
    if output.name != correction_id or not correction_id.startswith(
        f"{PARENT_PACKAGE_ID}-correction-"
    ):
        raise DwdCorrectionError("correction id is not bound to DWD v2.1")
    try:
        parsed_built_at = datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DwdCorrectionError("built_at must be an ISO timestamp") from exc
    if parsed_built_at.utcoffset() is None:
        raise DwdCorrectionError("built_at must include a UTC offset")
    parent_identity = _parent_identity(parent)
    source_files = set(_REVISION_MEMBERS) | set(_REVISION_METADATA)
    source_checksums = {
        name: _sha256(_physical_file(normalization_revision, name)) for name in sorted(source_files)
    }
    revision_identity = _revision_identity(normalization_revision)
    if any(
        _sha256(normalization_revision / name) != digest
        for name, digest in source_checksums.items()
    ):
        raise DwdCorrectionError("normalization revision changed during source verification")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{correction_id}.", dir=output.parent))
    try:
        row_counts, audit = _build_payload(
            parent=parent,
            revision=normalization_revision,
            staging=staging,
            correction_id=correction_id,
            built_at=built_at,
        )
        # The two replay members retain their existing paths. Preserve metadata as
        # exact bytes, including whitespace; never reconstruct a source manifest.
        for name in _REVISION_METADATA:
            shutil.copyfile(
                _physical_file(normalization_revision, name), staging / "payload/replay" / name
            )
        if any(
            _sha256(staging / "payload/replay" / name) != digest
            for name, digest in source_checksums.items()
        ):
            raise DwdCorrectionError("normalization revision bytes changed during copy")
        _write_json(
            staging / "evidence/external-findings-audit.json",
            {
                "schema_version": 1,
                "kind": "dwd_v2_1_external_findings_audit",
                "parent": parent_identity,
                "normalization_revision": revision_identity,
                **audit,
            },
        )
        files = _file_inventory(staging)
        content_identity = {
            "schema_version": CORRECTION_SCHEMA_VERSION,
            "source_closure": _SOURCE_CLOSURE,
            "parent": parent_identity,
            "normalization_revision": revision_identity,
            "files": files,
        }
        content_digest = _canonical_digest(content_identity)
        manifest = {
            "schema_version": CORRECTION_SCHEMA_VERSION,
            "kind": "dwd_base_correction",
            "correction_id": correction_id,
            "built_at": built_at,
            "code_revision": code_revision,
            "parent": parent_identity,
            "normalization_revision": revision_identity,
            "source_closure": _SOURCE_CLOSURE,
            "execution_authorized": False,
            "application": {
                "mode": "business_key_replacement_and_record_tombstone",
                "parent_mutation": False,
                "replacement_keys": {
                    "dwd_tradability_state": ["listing_id", "calendar_session_id"],
                    "dwd_corporate_action_term": ["corporate_action_term_id"],
                },
                "delete_keys": {
                    "dwd_corporate_action": ["corporate_action_id"],
                    "dwd_corporate_action_term": ["corporate_action_term_id"],
                    "dwd_disclosure": ["disclosure_id"],
                    "dwd_disclosure_subject": ["disclosure_subject_id"],
                },
            },
            "row_counts": row_counts,
            "files": files,
            "content_digest": content_digest,
        }
        _write_json(staging / "manifest.json", manifest)
        manifest_sha = _sha256(staging / "manifest.json")
        with (staging / "READY").open("x", encoding="ascii") as stream:
            stream.write(manifest_sha + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        verified = verify_dwd_correction(staging, parent=parent, deep=True)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return replace(verified, root=output)


def verify_dwd_correction(root: Path, *, parent: Path, deep: bool = False) -> DwdCorrectionBundle:
    """Verify integrity and report legacy source-proof limits explicitly.

    v2 always verifies retained revision bytes, lineage and authority; deep also
    verifies the effective overlay. A v1 pass never proves retained source scope.
    """

    if root.is_symlink():
        raise DwdCorrectionError("DWD correction root must be a physical directory")
    root = root.resolve()
    parent = parent.resolve()
    if not root.is_dir() or root.is_symlink():
        raise DwdCorrectionError("DWD correction root must be a physical directory")
    for control in ("manifest.json", "READY"):
        _physical_file(root, control)
    if any(path.is_symlink() for path in root.rglob("*")):
        raise DwdCorrectionError("DWD correction contains a symlink")
    parent_identity = _parent_identity(parent)
    manifest = _read_json(root / "manifest.json")
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version not in (1, CORRECTION_SCHEMA_VERSION):
        raise DwdCorrectionError("unsupported DWD correction schema version")
    manifest_sha = _sha256(root / "manifest.json")
    if (root / "READY").read_text(encoding="ascii").strip() != manifest_sha:
        raise DwdCorrectionError("DWD correction READY differs from manifest")
    if manifest.get("kind") != "dwd_base_correction" or manifest.get("parent") != parent_identity:
        raise DwdCorrectionError("DWD correction identity differs")
    correction_id = str(manifest.get("correction_id") or "")
    if not correction_id.startswith(f"{PARENT_PACKAGE_ID}-correction-"):
        raise DwdCorrectionError("DWD correction id is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise DwdCorrectionError("DWD correction has no file inventory")
    declared: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise DwdCorrectionError("DWD correction inventory contains a non-object")
        relative = str(item.get("path") or "")
        if relative in declared:
            raise DwdCorrectionError(f"duplicate correction file path: {relative}")
        declared.add(relative)
        path = _physical_file(root, relative)
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item.get("size_bytes")
            or (
                (
                    deep
                    or schema_version == CORRECTION_SCHEMA_VERSION
                    and relative.startswith("payload/replay/")
                )
                and _sha256(path) != item.get("sha256")
            )
        ):
            raise DwdCorrectionError(f"correction file differs: {relative}")
    physical = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if physical != declared | {"manifest.json", "READY"}:
        raise DwdCorrectionError("DWD correction physical inventory differs")
    content_identity = {
        "parent": manifest.get("parent"),
        "normalization_revision": manifest.get("normalization_revision"),
        "files": files,
    }
    if schema_version == CORRECTION_SCHEMA_VERSION:
        if (
            manifest.get("source_closure") != _SOURCE_CLOSURE
            or manifest.get("execution_authorized") is not False
        ):
            raise DwdCorrectionError("DWD correction source closure or authority differs")
        content_identity.update(schema_version=schema_version, source_closure=_SOURCE_CLOSURE)
        closure_files = {
            f"payload/replay/{name}" for name in (*_REVISION_MEMBERS, *_REVISION_METADATA)
        }
        if not closure_files <= declared:
            raise DwdCorrectionError("DWD correction normalization revision closure is incomplete")
        if _revision_identity(root / "payload/replay") != manifest.get("normalization_revision"):
            raise DwdCorrectionError("DWD correction normalization manifest identity differs")
        source_closure_status = "verified_normalization_revision_closure"
    else:
        if "source_closure" in manifest or any(
            f"payload/replay/{name}" in declared for name in _REVISION_METADATA
        ):
            raise DwdCorrectionError("legacy correction cannot declare a v2 source closure")
        source_closure_status = "legacy_manifest_unretained"
    if manifest.get("content_digest") != _canonical_digest(content_identity):
        raise DwdCorrectionError("DWD correction content digest differs")
    if deep:
        _verify_overlay_semantics(parent=parent, correction=root)
    return DwdCorrectionBundle(
        root=root,
        correction_id=correction_id,
        manifest_sha256=manifest_sha,
        content_digest=str(manifest["content_digest"]),
        row_counts={str(k): int(v) for k, v in manifest["row_counts"].items()},
        source_closure_status=source_closure_status,
    )


def _verify_overlay_semantics(*, parent: Path, correction: Path) -> None:
    duckdb, _ = _dependencies()
    connection = duckdb.connect()
    paths = _base_paths(parent)
    root = _sql_path(correction)
    try:
        state_replacement = (
            f"{root}/payload/tradability_state/dwd_tradability_state_replacement.parquet"
        )
        expected, actual, invalid = connection.execute(
            f"""
            WITH expected AS (
              SELECT s.listing_id,s.calendar_session_id
              FROM read_parquet('{paths["state"]}', hive_partitioning=true) s
              JOIN read_parquet('{paths["bar"]}', hive_partitioning=true) b
                USING (listing_id,calendar_session_id)
              WHERE s.knowledge_to IS NULL AND s.is_tradable
                AND b.knowledge_to IS NULL AND b.interval_size='1d'
                AND b.price_basis='UNADJUSTED'
                AND (b.volume IS NULL OR b.volume<=0 OR b.turnover IS NULL OR b.turnover<=0)
            ), replacement AS (
              SELECT * FROM read_parquet('{state_replacement}')
            )
            SELECT (SELECT count(*) FROM expected),(SELECT count(*) FROM replacement),
                   (SELECT count(*) FROM replacement
                    WHERE is_tradable OR can_buy OR can_sell
                       OR reason_code<>'ZERO_ACTIVITY_OBSERVATION'
                       OR upper_price IS NOT NULL OR lower_price IS NOT NULL)
            """
        ).fetchone()
        if expected != actual or invalid:
            raise DwdCorrectionError(
                "tradability overlay differs: "
                f"expected={expected} actual={actual} invalid={invalid}"
            )
        duplicate_groups, zero_terms = connection.execute(
            f"""
            WITH deleted_term AS (
              SELECT record_id FROM read_parquet(
                '{root}/payload/disclosure_company_event/dwd_corporate_action_term_delete.parquet'
              )
            ), deleted_action AS (
              SELECT record_id FROM read_parquet(
                '{root}/payload/disclosure_company_event/dwd_corporate_action_delete.parquet'
              )
            ), replacement AS (
              SELECT * FROM read_parquet(
                '{root}/payload/disclosure_company_event/dwd_corporate_action_term_replacement.parquet'
              )
            ), effective_term AS (
              SELECT * FROM read_parquet('{paths["term"]}', hive_partitioning=true)
              WHERE corporate_action_term_id NOT IN (SELECT record_id FROM deleted_term)
                AND corporate_action_term_id NOT IN (
                  SELECT corporate_action_term_id FROM replacement
                )
              UNION ALL BY NAME SELECT * FROM replacement
            ), facts AS (
              SELECT a.security_id,CAST(a.effective_at AS DATE) effective_date,
                     CAST(a.announced_at AS DATE) announced_date,t.term_type,
                     coalesce(t.amount_per_share,
                       t.ratio_numerator/nullif(t.ratio_denominator,0))::DECIMAL(38,10)
                       AS term_value,count(DISTINCT a.corporate_action_id) action_count
              FROM read_parquet('{paths["action"]}', hive_partitioning=true) a
              JOIN effective_term t USING (corporate_action_id)
              WHERE a.corporate_action_id NOT IN (SELECT record_id FROM deleted_action)
              GROUP BY ALL
            )
            SELECT count(*) FILTER (WHERE action_count>1),
                   count(*) FILTER (WHERE term_value=0)
            FROM facts
            """
        ).fetchone()
        if duplicate_groups or zero_terms:
            raise DwdCorrectionError(
                "corporate-action overlay is not unique/non-zero: "
                f"duplicates={duplicate_groups} zero_terms={zero_terms}"
            )
    finally:
        connection.close()
