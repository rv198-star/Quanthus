"""Immutable disclosure corrections and independently qualified weekly financial DWD.

Uses existing ODS bytes and the production domain adapters. No vendor requests,
Base edits, or rewriting past knowledge versions occur in this builder.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.pipelines.p10_delivery_common import (
    BASE23_CONTENT,
    BASE23_MANIFEST,
    atomic,
    digest,
    encoded,
    file_digest,
    object_at,
    prepare_export,
    require_revision,
    safe_path,
    seal,
    verify_seal,
)
from quanthus.pipelines.securities_warehouse.daily_core_domain import _base_files, _verify_base
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialStatementSource,
    decode_financial_envelope,
    expand_financial_envelope,
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
)
from quanthus.pipelines.securities_warehouse.listed_company import _stable_id

KIND = "p10_base23_domain_continuation_v1"
POLICY = "p10-acquisition-prevention-v1"
PURPOSE = "gap_aware_non_execution_development"


def stamp(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("knowledge clock requires timezone")
    return result.astimezone(timezone.utc)


def metadata(row: dict[str, Any]) -> str:
    return digest(
        encoded(
            {
                key: str(row[key])
                for key in ("stock_code", "ann_id", "title", "publish_time", "announce_date")
            }
        )
    )


def _identity_rows(
    base: Path, config: Path
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow.parquet as pq

    native, _config = _verify_base(base, config)
    rows = {}
    for table in ("dwd_listing", "dwd_security", "dwd_security_identifier"):
        rows[table] = [
            row
            for path in _base_files(base, native, table)
            for row in pq.ParquetFile(path).read().to_pylist()
        ]
    binding = dict(native.binding) if hasattr(native, "binding") else {}
    if (
        binding.get("manifest_sha256") != BASE23_MANIFEST
        or binding.get("content_digest") != BASE23_CONTENT
    ):
        raise ValueError("domain continuation requires exact Base 2.3")
    return rows, binding


def _company(rows: dict[str, list[dict[str, Any]]], code: str, day: date) -> tuple[str, str]:
    securities = {row["security_id"]: row["company_id"] for row in rows["dwd_security"]}
    matching = [
        row
        for row in rows["dwd_listing"]
        if row["listing_code"] == code
        and row["effective_from"].astimezone(ZoneInfo("Asia/Shanghai")).date() <= day
        and (
            row["effective_to"] is None
            or day < row["effective_to"].astimezone(ZoneInfo("Asia/Shanghai")).date()
        )
    ]
    resolved = {(row["security_id"], securities[row["security_id"]]) for row in matching}
    if len(resolved) != 1:
        raise ValueError(f"DISCLOSURE_COMPANY_IDENTITY_UNAVAILABLE:{code}")
    return resolved.pop()


def disclosure_candidates(
    rows: list[dict[str, Any]], previous: dict[str, dict[str, Any]], covered_after: date
) -> list[dict[str, Any]]:
    """Only old-business-date new/changed documents; fresh daily rows stay in daily-core."""
    selected = []
    keys = set()
    for row in rows:
        key = (str(row["stock_code"]), str(row["ann_id"]))
        if key in keys:
            raise ValueError("DUPLICATE_SOURCE_DOCUMENT")
        keys.add(key)
        if date.fromisoformat(str(row["announce_date"])) > covered_after:
            continue
        prior = previous.get(str(row["ann_id"]))
        if prior is None:
            selected.append({**row, "correction_kind": "late_observation"})
        elif any(
            str(prior[field]) != str(row[field])
            for field in ("title", "publish_time", "announce_date")
        ):
            selected.append({**row, "correction_kind": "metadata_revision"})
    return selected


def _history_bindings(core: Path, products: Path, through: date) -> list[dict[str, Any]]:
    result = []
    for kind, root, pointer_dir in [
        ("daily", core, core / "date-pointers"),
        ("correction", products / "disclosure", products / "disclosure/date-pointers"),
    ]:
        for pointer in sorted(pointer_dir.glob("*.json")):
            value = object_at(pointer)
            if date.fromisoformat(value["market_date"]) > through:
                continue
            path = safe_path(root, value["manifest_path"])
            if (
                file_digest(path) != value["manifest_sha256"]
                or (path.parent / "READY").read_text().strip() != value["manifest_sha256"]
            ):
                raise ValueError("disclosure history pointer identity differs")
            manifest = object_at(path)
            members = []
            for item in manifest["files"]:
                if "/dwd_disclosure/" in item["path"]:
                    member = safe_path(path.parent, item["path"])
                    if file_digest(member) != item["sha256"]:
                        raise ValueError("disclosure history physical member differs")
                    members.append({"path": str(member), "sha256": item["sha256"]})
            result.append(
                {
                    "kind": kind,
                    "manifest_path": str(path),
                    "manifest_sha256": value["manifest_sha256"],
                    "members": members,
                    "covered_after_date": manifest.get("covered_after_date"),
                }
            )
    return result


def _previous_disclosures(core: Path, products: Path, through: date) -> dict[str, dict[str, Any]]:
    from zoneinfo import ZoneInfo

    import pyarrow.parquet as pq

    latest: dict[str, dict[str, Any]] = {}
    paths: list[Path] = []
    for pointer in sorted((core / "date-pointers").glob("*.json")):
        value = object_at(pointer)
        if date.fromisoformat(value["market_date"]) <= through:
            package = safe_path(core, value["manifest_path"]).parent
            manifest = object_at(package / "manifest.json")
            paths.extend(
                safe_path(package, item["path"])
                for item in manifest["files"]
                if "/dwd_disclosure/" in item["path"]
            )
    for pointer in sorted((products / "disclosure/date-pointers").glob("*.json")):
        value = object_at(pointer)
        if date.fromisoformat(value["market_date"]) <= through:
            package = safe_path(products / "disclosure", value["manifest_path"]).parent
            verify(package)
            member = package / "payload/dwd_disclosure/part-00000.parquet"
            if member.exists():
                paths.append(member)
    for path in paths:
        for row in pq.ParquetFile(path).read().to_pylist():
            key = str(row["source_document_id"])
            if key in latest and latest[key]["knowledge_from"] > row["knowledge_from"]:
                continue
            pub = row["announced_at"].astimezone(ZoneInfo("Asia/Shanghai"))
            latest[key] = {
                "title": row["title"],
                "publish_time": pub.strftime("%Y-%m-%d %H:%M:%S"),
                "announce_date": pub.date().isoformat(),
                "knowledge_from": row["knowledge_from"],
            }
    return latest


def _write_disclosures(
    stage: Path,
    *,
    source: Path,
    core: Path,
    products: Path,
    market_date: date,
    identities: dict[str, Any],
    observed: datetime,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    import pyarrow as pa
    import pyarrow.parquet as pq

    eligible = [
        path
        for path in sorted((core / "date-pointers").glob("*.json"))
        if path.stem <= market_date.isoformat()
    ]
    if not eligible:
        raise ValueError("disclosure continuation requires a daily-core identity")
    pointer = object_at(eligible[-1])
    candidates = disclosure_candidates(
        pq.ParquetFile(source).read().to_pylist(),
        _previous_disclosures(core, products, market_date),
        market_date,
    )
    source_sha = file_digest(source)
    rows, subjects = [], []
    for item in sorted(candidates, key=lambda row: (row["stock_code"], row["ann_id"])):
        security, company = _company(
            identities, str(item["stock_code"]), date.fromisoformat(item["announce_date"])
        )
        effective = (
            datetime.fromisoformat(item["publish_time"])
            .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            .astimezone(timezone.utc)
        )
        disclosure = _stable_id("disclosure", "announcement_archive", str(item["ann_id"]))
        lineage = {
            "record_source": "cninfo.hisAnnouncement",
            "source_record_id": "announcements_delta.parquet#ann_id=" + str(item["ann_id"]),
            "source_revision": POLICY,
            "source_artifact_sha256": source_sha,
            "knowledge_from": observed,
            "knowledge_to": None,
            "effective_at": effective,
            "announced_at": effective,
            "correction_kind": item["correction_kind"],
            "metadata_sha256": metadata(item),
            "body_sha256": item.get("body_sha256"),
            "recorded_provider_acquired_at": str(item["acquired_at"]),
        }
        rows.append(
            {
                **lineage,
                "disclosure_id": disclosure,
                "company_id": company,
                "disclosure_type": "announcement",
                "source_document_id": str(item["ann_id"]),
                "title": item["title"],
                "document_uri": item["url"],
                "language": "zh-CN",
            }
        )
        subjects.append(
            {
                **lineage,
                "disclosure_subject_id": _stable_id(
                    "disclosure_subject", disclosure, "SECURITY", security
                ),
                "disclosure_id": disclosure,
                "subject_type": "SECURITY",
                "security_id": security,
                "corporate_action_id": None,
                "financial_report_id": None,
            }
        )
    # An empty correction is represented by its ledger, not a fake placeholder row.
    for table, values in [("dwd_disclosure", rows), ("dwd_disclosure_subject", subjects)]:
        if values:
            path = stage / "payload" / table / "part-00000.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist(values), path, compression="zstd")
    return {
        "row_counts": {"dwd_disclosure": len(rows), "dwd_disclosure_subject": len(subjects)},
        "corrections": dict(Counter(row["correction_kind"] for row in rows)),
        "body_revision_unknown": True,
        "daily_manifest_sha256": pointer["manifest_sha256"],
    }


def _write_financial(
    stage: Path,
    *,
    source: Path,
    receipts: Path,
    identities: dict[str, Any],
    observed: datetime,
    market_date: date,
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    resolver = financial_company_resolver_from_listed_company_rows(
        identities["dwd_security"], identities["dwd_security_identifier"]
    )
    receipt_rows = pq.ParquetFile(receipts).read().to_pylist()
    wanted = {}
    for row in receipt_rows:
        if row["dataset"] != "financial_statements":
            continue
        request = json.loads(row["request_json"])
        key = (row["security_id"], request["params"]["source"])
        if key in wanted or row["status"] not in {"success_nonempty", "justified_empty"}:
            raise ValueError("WEEKLY_RECEIPT_MATRIX_INVALID")
        wanted[key] = row
    schemas = financial_reporting_schemas()
    writers, paths = {}, {}
    for name, schema in schemas.items():
        path = stage / "payload" / name / "part-00000.parquet"
        path.parent.mkdir(parents=True)
        paths[name] = path
        writers[name] = pq.ParquetWriter(path, schema, compression="zstd")
    counts: Counter[str] = Counter()
    exceptions: Counter[str] = Counter()
    seen = set()
    source_sha = file_digest(source)
    try:
        for batch in pq.ParquetFile(source).iter_batches(batch_size=16):
            for original in batch.to_pylist():
                key = (original["security_id"], original["provider_source"])
                receipt = wanted.get(key)
                envelope = decode_financial_envelope(original)
                if (
                    key in seen
                    or receipt is None
                    or receipt["response_sha256"] != digest(envelope.raw_payload)
                ):
                    raise ValueError("WEEKLY_RAW_RECEIPT_CLOSURE_DIFFERS")
                seen.add(key)
                # Original capture clock stays in the source; this new product is visible now.
                row = {**original, "acquired_at": observed.isoformat()}
                expanded = expand_financial_envelope(
                    row,
                    source=FinancialStatementSource(
                        label="weekly_reused_raw",
                        path=source,
                        relative_path="sources/financial_statements.parquet",
                        source_sha256=source_sha,
                        source_revision=str(row["source_revision"]),
                        precedence="hotfix",
                    ),
                    company_resolver=resolver,
                    through_date=market_date,
                )
                for table, values in [
                    ("dwd_financial_report", expanded.reports),
                    ("dwd_financial_concept", expanded.concepts),
                    ("dwd_financial_fact", expanded.facts),
                ]:
                    if values:
                        writers[table].write_table(
                            pa.Table.from_pylist(values, schema=schemas[table])
                        )
                        counts[table] += len(values)
                exceptions.update(item["code"] for item in expanded.quality_exceptions)
    finally:
        for writer in writers.values():
            writer.close()
    if seen != set(wanted) or not seen:
        raise ValueError("WEEKLY_SOURCE_MATRIX_INCOMPLETE")
    codes = {key[0] for key in seen}
    if any(
        {source for code, source in seen if code == stock} != {"fzb", "lrb", "llb"}
        for stock in codes
    ):
        raise ValueError("WEEKLY_THREE_STATEMENT_MATRIX_INCOMPLETE")
    # Resolve shared concept rows once, and check physical key/FK closure in one local job.
    import duckdb

    con = duckdb.connect()
    con.execute("SET threads=1")
    con.execute("SET memory_limit='1GB'")
    con.execute("SET temp_directory=?", [str(stage / "tmp")])
    try:
        concepts = paths["dwd_financial_concept"]
        unique = concepts.with_name("unique.parquet")
        con.execute(
            "COPY (SELECT DISTINCT * FROM read_parquet(?)) TO '"
            + str(unique).replace("'", "''")
            + "' (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(concepts)],
        )
        os.replace(unique, concepts)
        counts["dwd_financial_concept"] = pq.ParquetFile(concepts).metadata.num_rows
        for table, version_key in [
            ("dwd_financial_report", "financial_report_id"),
            ("dwd_financial_fact", "financial_fact_id"),
        ]:
            duplicate_result = con.execute(
                f"SELECT count(*) FROM (SELECT {version_key},knowledge_from "
                "FROM read_parquet(?) GROUP BY ALL HAVING count(*)>1)",
                [str(paths[table])],
            ).fetchone()
            assert duplicate_result is not None
            if duplicate_result[0]:
                raise ValueError("WEEKLY_DWD_VERSION_KEY_DUPLICATE")
        orphan_result = con.execute(
            "SELECT count(*) FROM read_parquet(?) f WHERE NOT EXISTS "
            "(SELECT 1 FROM read_parquet(?) r WHERE "
            "r.financial_report_id=f.financial_report_id) OR NOT EXISTS "
            "(SELECT 1 FROM read_parquet(?) c WHERE "
            "c.financial_concept_id=f.financial_concept_id)",
            [str(paths["dwd_financial_fact"]), str(paths["dwd_financial_report"]), str(concepts)],
        ).fetchone()
        assert orphan_result is not None
        if orphan_result[0]:
            raise ValueError("WEEKLY_DWD_FOREIGN_KEY_FAILURE")
    finally:
        con.close()
        shutil.rmtree(stage / "tmp", ignore_errors=True)
    return {
        "row_counts": dict(counts),
        "source_envelopes": len(seen),
        "source_securities": len(codes),
        "quality_exceptions": dict(exceptions),
        "literal_source_native_only": True,
        "canonical_and_historical_pit_admission": False,
        "raw_reused_without_supplier_requests": True,
        "unavailable_domains": ["F10", "shareholder_complete_matrix"],
    }


def verify(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    manifest = verify_seal(root, KIND)
    require_revision(manifest["code_revision"])
    if (
        manifest.get("policy_id") != POLICY
        or manifest.get("execution_authorized") is not False
        or manifest.get("consumer_purpose") != PURPOSE
    ):
        raise ValueError("domain continuation purpose differs")
    if (
        manifest["base"]["manifest_sha256"] != BASE23_MANIFEST
        or manifest["base"]["content_digest"] != BASE23_CONTENT
    ):
        raise ValueError("domain continuation Base identity differs")
    for table, count in manifest["audit"]["row_counts"].items():
        path = root / "payload" / table / "part-00000.parquet"
        actual = pq.ParquetFile(path).metadata.num_rows if path.exists() else 0
        if actual != count:
            raise ValueError("domain continuation physical census differs")
    return {**manifest, "manifest_sha256": file_digest(root / "manifest.json")}


def _publish_domain(
    root: Path, dest: Path, result: dict[str, Any], prior: dict[str, Any] | None
) -> None:
    prepare_export(root, dest)
    value = {
        "kind": "p10_domain_continuation_current_v1",
        "domain": result["domain"],
        "market_date": result["market_date"],
        "manifest_path": (dest / "manifest.json").relative_to(root).as_posix(),
        "manifest_sha256": result["manifest_sha256"],
        "content_digest": result["content_digest"],
        "prior": prior,
    }
    atomic(root / "date-pointers" / (result["market_date"] + ".json"), value)
    atomic(root / "CURRENT_DOMAIN.json", value)


def build(
    *,
    domain: str,
    source: Path,
    receipts: Path | None,
    core: Path,
    products: Path,
    base: Path,
    config: Path,
    day: date,
    code_revision: str,
) -> dict[str, Any]:
    require_revision(code_revision)
    if domain not in {"disclosure", "financial"}:
        raise ValueError("unsupported continuation domain")
    if domain == "financial" and receipts is None:
        raise ValueError("financial receipts required")
    identities, binding = _identity_rows(base, config)
    inputs: dict[str, Any] = {
        "source_sha256": file_digest(source),
        "base": binding,
        "config_sha256": file_digest(config),
        "code_revision": code_revision,
        "domain": domain,
        "market_date": day.isoformat(),
        "policy_id": POLICY,
        "policy_sha256": file_digest(
            Path(__file__).resolve().parents[3]
            / "config/pipelines/p10-acquisition-prevention.v1.json"
        ),
    }
    history = _history_bindings(core, products, day) if domain == "disclosure" else []
    inputs["upstream_history"] = [
        record
        for record in history
        if record["kind"] == "daily" or ("/" + day.isoformat() + "/") not in record["manifest_path"]
    ]
    if receipts:
        inputs["receipts_sha256"] = file_digest(receipts)
    root = products / domain
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".publish.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        pointer = root / "CURRENT_DOMAIN.json"
        prior = object_at(pointer) if pointer.exists() else None
        if prior:
            prior_manifest = verify(safe_path(root, prior["manifest_path"]).parent)
            previous_inputs = {
                key: value for key, value in prior_manifest["inputs"].items() if key != "history"
            }
            if previous_inputs == inputs:
                return prior_manifest
            if prior_manifest["market_date"] > day.isoformat():
                raise ValueError("domain pointer regression")
        dated = root / "date-pointers" / (day.isoformat() + ".json")
        if dated.exists():
            dated_pointer = object_at(dated)
            dated_root = safe_path(root, dated_pointer["manifest_path"]).parent
            dated_result = verify(dated_root)
            dated_inputs = {
                key: value for key, value in dated_result["inputs"].items() if key != "history"
            }
            if dated_inputs == inputs and dated_result["prior"] == prior:
                _publish_domain(root, dated_root, dated_result, prior)
                return dated_result
        inputs["history"] = history
        identity = digest(encoded([inputs, prior]))[7:23]
        dest = root / "packages" / day.isoformat() / identity
        if shutil.disk_usage(root).free < 4 * 1024**3 + 3 * source.stat().st_size:
            raise ValueError("domain continuation insufficient reserve")
        if dest.exists():
            recovered = verify(dest)
            if recovered["inputs"] != inputs or recovered["prior"] != prior:
                raise ValueError("orphan continuation identity differs")
            _publish_domain(root, dest, recovered, prior)
            return recovered
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".continuation-", dir=dest.parent))
        try:
            observed = datetime.now(timezone.utc)
            (stage / "sources").mkdir()
            shutil.copyfile(source, stage / "sources" / source.name)
            frozen_source = stage / "sources" / source.name
            if file_digest(frozen_source) != inputs["source_sha256"]:
                raise ValueError("source changed during freeze")
            if receipts:
                shutil.copyfile(receipts, stage / "sources" / receipts.name)
                frozen_receipts = stage / "sources" / receipts.name
                if file_digest(frozen_receipts) != inputs["receipts_sha256"]:
                    raise ValueError("receipts changed during freeze")
            audit = (
                _write_disclosures(
                    stage,
                    source=frozen_source,
                    core=core,
                    products=products,
                    market_date=day,
                    identities=identities,
                    observed=observed,
                )
                if domain == "disclosure"
                else _write_financial(
                    stage,
                    source=frozen_source,
                    receipts=frozen_receipts,
                    identities=identities,
                    observed=observed,
                    market_date=day,
                )
            )
            atomic(stage / "audit.json", audit)
            if domain == "disclosure" and _history_bindings(core, products, day) != history:
                raise ValueError("disclosure history changed during construction")
            seal(
                stage,
                {
                    "schema_version": 1,
                    "kind": KIND,
                    "domain": domain,
                    "policy_id": POLICY,
                    "market_date": day.isoformat(),
                    "package_id": identity,
                    "base": binding,
                    "inputs": inputs,
                    "code_revision": code_revision,
                    "audit": audit,
                    "prior": prior,
                    "consumer_purpose": PURPOSE,
                    "execution_authorized": False,
                    "knowledge_from": observed.isoformat(),
                    "historical_pit_authorized": False,
                    "all_business_domains_complete": False,
                },
            )
            result = verify(stage)
            os.replace(stage, dest)
            _publish_domain(root, dest, result, prior)
            return result
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "core", "products", "base", "config"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--domain", choices=("disclosure", "financial"), required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args()
    result = build(**vars(args))
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("domain", "market_date", "manifest_sha256", "content_digest", "audit")
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
