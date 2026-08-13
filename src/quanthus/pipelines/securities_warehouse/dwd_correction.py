"""Build and verify immutable corrections bound to the DWD v2.1 Base."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


class DwdCorrectionError(RuntimeError):
    """Raised when a DWD correction cannot be proved against its parent."""


PARENT_PACKAGE_ID = "dwd-base-202607-v2.1"
PARENT_MANIFEST_SHA256 = "sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b"
PARENT_CONTENT_DIGEST = "sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9"
NORMALIZED_REVISION = "corporate_action_replay_2012_20260730_v3"
NORMALIZED_POLICY = "p1-corporate-action-normalization-20260730-v3"


@dataclass(frozen=True)
class DwdCorrectionBundle:
    root: Path
    correction_id: str
    manifest_sha256: str
    content_digest: str
    row_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "correction_id": self.correction_id,
            "manifest_sha256": self.manifest_sha256,
            "content_digest": self.content_digest,
            "row_counts": self.row_counts,
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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
    if path.is_absolute() or not path.parts or ".." in path.parts:
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


def _revision_identity(revision: Path) -> dict[str, str]:
    manifest_path = revision / "manifest.json"
    manifest = _read_json(manifest_path)
    if (
        manifest.get("revision") != NORMALIZED_REVISION
        or manifest.get("ready_for_snapshot") is not True
        or (manifest.get("policy") or {}).get("normalization_policy_version") != NORMALIZED_POLICY
    ):
        raise DwdCorrectionError("corporate-action normalization revision differs")
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        raise DwdCorrectionError("normalization revision has no checksums")
    for name in ("corporate_actions_normalized.parquet", "corporate_action_exceptions.parquet"):
        if checksums.get(name) != _sha256(revision / name):
            raise DwdCorrectionError(f"normalization revision checksum differs: {name}")
    return {
        "revision": NORMALIZED_REVISION,
        "manifest_sha256": _sha256(manifest_path),
        "policy_version": NORMALIZED_POLICY,
    }


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
        if not path.is_file() or path.name in {"manifest.json", "READY"}:
            continue
        record: dict[str, Any] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        if path.suffix == ".parquet":
            record["row_count"] = pq.ParquetFile(path).metadata.num_rows
            record["format"] = "parquet"
        else:
            record["format"] = "json"
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
    revision_identity = _revision_identity(normalization_revision)
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
            "parent": parent_identity,
            "normalization_revision": revision_identity,
            "files": files,
        }
        content_digest = _canonical_digest(content_identity)
        manifest = {
            "schema_version": 1,
            "kind": "dwd_base_correction",
            "correction_id": correction_id,
            "built_at": built_at,
            "code_revision": code_revision,
            "parent": parent_identity,
            "normalization_revision": revision_identity,
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
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_dwd_correction(output, parent=parent, deep=True)


def verify_dwd_correction(root: Path, *, parent: Path, deep: bool = False) -> DwdCorrectionBundle:
    """Verify package integrity and, when deep, the effective overlay semantics."""

    root = root.resolve()
    parent = parent.resolve()
    if not root.is_dir() or root.is_symlink():
        raise DwdCorrectionError("DWD correction root must be a physical directory")
    parent_identity = _parent_identity(parent)
    manifest = _read_json(root / "manifest.json")
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
        path = root / _safe_relative(relative)
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item.get("size_bytes")
            or (deep and _sha256(path) != item.get("sha256"))
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
