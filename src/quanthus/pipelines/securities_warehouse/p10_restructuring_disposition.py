"""P10 #561: one non-beneficial restructuring and one unbounded resume session.

This is a job-local composite input. Its immutable parent is an explicit argument,
not a portable ADS publication, a cache fallback, or a changed P1 ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from quanthus.contracts import SnapshotRef

from . import p10_rights_replay_input as physical
from . import p10_suspended_action_input as parent

VERSION = "p10-000792-qualified-restructuring-input-v1"
SOURCE_VERSION = "p10-000792-restructuring-disposition-source-v1"
PARENT_SHA = "fd477875a3cd8dd452889258afc37524ffc8fff5c54dc6656ef0cbe04ead847c"
EXCEPTION = "sha256:d8b651f797db613804810f68950b8195916209c5d908cc78531b596b43c5506c"
ACTION = "658be64c26ad94145b6de03a"
DOCS = {
    "1207399015": "005cc6c67fefea6582b1991ea0e711591e865574381748d0326ee5101f8b49a5",
    "1207399016": "c9f12703ec16f0f44a0bfd98a6264081ee61e4b6234332398ddb6cefe5320dcf",
    "1210709225": "adb62c7d836b0e4bb58d810e79b71b576f254a2e503e4343e6b80598f548a98b",
}
LIMIT_FIELDS = {
    "upper_limit_price",
    "lower_limit_price",
    "upper_limit_pct",
    "lower_limit_pct",
    "knowledge_cutoff",
    "revision",
}


def sha(path: Path) -> str:
    return physical._sha(path)


def parse_terms(texts: dict[str, str]) -> dict[str, Any]:
    """Parse exact clauses; a supplier transfer ratio is not an entitlement."""
    if set(texts) != set(DOCS):
        raise ValueError("RESTRUCTURING_DOCUMENT_SET")
    compact = {k: re.sub(r"\s+", "", v) for k, v in texts.items()}
    implementation, no_ex, resume = (compact[k] for k in DOCS)
    if any("000792" not in v for v in compact.values()) or not all(
        "不向原股东分配" in v and "不再对公司股票进行除权处理" in v for v in (implementation, no_ex)
    ):
        raise ValueError("RESTRUCTURING_BENEFICIARY_OR_REFERENCE")
    record = re.findall(r"股权登记日为(\d{4})年(\d{1,2})月(\d{1,2})日", implementation)
    listed = re.findall(r"转增股本上市日为(\d{4})年(\d{1,2})月(\d{1,2})日", implementation)
    if record != [("2020", "3", "30")] or listed != [("2020", "3", "31")]:
        raise ValueError("RESTRUCTURING_DATES")
    if (
        "股票自2021年8月10日起恢复上市" not in resume
        or "首日（即2021年8月10日）不实行价格涨跌幅限制" not in resume
        or "次一交易日（即2021年8月11日）起，日涨跌幅限制为10%" not in resume
    ):
        raise ValueError("RESTRUCTURING_EXACT_RESUME_RULE")
    return {
        "security_id": "000792",
        "supplier_observation_date": "2020-03-25",
        "record_date": "2020-03-30",
        "new_share_listing_date": "2020-03-31",
        "original_shareholders_receive_new_shares": False,
        "ordinary_shareholder_cash_entitlement": "0",
        "ordinary_shareholder_bonus_entitlement": "0",
        "stock_exright_adjustment": False,
        "resume_unbounded_date": "2021-08-10",
        "ordinary_limit_resumes": "2021-08-11",
        "actual_share_credit_date": None,
        "historical_known_at": None,
    }


def load_source(root: Path, expected: str) -> dict[str, Any]:
    paths = physical._files(root)
    if sha(root / "manifest.json") != expected:
        raise ValueError("RESTRUCTURING_SOURCE_IDENTITY")
    m = json.loads((root / "manifest.json").read_bytes())
    if (
        m.get("kind") != SOURCE_VERSION
        or m.get("historical_PIT_claim") is not False
        or m.get("production_authorized") is not False
    ):
        raise ValueError("RESTRUCTURING_SOURCE_SCOPE")
    declared = {"manifest.json", "SHA256SUMS"}
    for row in m["files"]:
        name = row["path"]
        if Path(name).name != name or name in declared:
            raise ValueError("RESTRUCTURING_SOURCE_MEMBER")
        p = root / name
        if p.stat().st_size != row["bytes"] or sha(p) != row["sha256"]:
            raise ValueError("RESTRUCTURING_SOURCE_BYTES")
        declared.add(name)
    if {p.name for p in paths} != declared or sum(p.stat().st_size for p in paths) > 16 * 1024**2:
        raise ValueError("RESTRUCTURING_SOURCE_PHYSICAL_SET")
    sums = "".join(
        f"{r['sha256']}  {r['path']}\n" for r in sorted(m["files"], key=lambda r: r["path"])
    )
    if (root / "SHA256SUMS").read_text() != sums + f"{expected}  manifest.json\n":
        raise ValueError("RESTRUCTURING_SOURCE_CHECKSUMS")
    if {d["notice_id"] for d in m["documents"]} != set(DOCS) or len(m["documents"]) != 3:
        raise ValueError("RESTRUCTURING_DOCUMENT_SET")
    texts, clocks = {}, []
    for doc in m["documents"]:
        aid = doc["notice_id"]
        if sha(root / doc["pdf_member"]) != DOCS[aid]:
            raise ValueError("RESTRUCTURING_OFFICIAL_PDF")
        metadata = json.loads((root / doc["metadata_member"]).read_bytes())
        matches = [
            a
            for a in metadata["announcements"]
            if str(a["announcementId"]) == aid and a["secCode"] == "000792"
        ]
        if len(matches) != 1:
            raise ValueError("RESTRUCTURING_OFFICIAL_METADATA")
        entry = matches[0]
        detail = (root / doc["detail_member"]).read_text()
        bases = re.findall(r"var v3_cninfo = [\"']([^\"']+)", detail)
        link = json.loads((root / f"publisher-link-{aid}.json").read_bytes())
        if (
            len(set(bases)) != 1
            or link["url"]
            != urljoin(
                bases[0].replace("http://", "https://", 1).rstrip("/") + "/", entry["adjunctUrl"]
            )
            or not link["url"].startswith("https://static.cninfo.com.cn/finalpage/")
            or link["metadata_sha256"] != sha(root / doc["metadata_member"])
            or link["detail_sha256"] != sha(root / doc["detail_member"])
        ):
            raise ValueError("RESTRUCTURING_PUBLISHER_LINK")
        text = subprocess.check_output(
            ["pdftotext", "-layout", str(root / doc["pdf_member"]), "-"], timeout=30
        ).decode("utf-8")
        if text != (root / doc["text_member"]).read_text():
            raise ValueError("RESTRUCTURING_PDF_TEXT_MISMATCH")
        texts[aid] = text
        clock = datetime.fromisoformat(doc["acquired_at"])
        if clock.utcoffset() is None:
            raise ValueError("RESTRUCTURING_SOURCE_CLOCK")
        clocks.append(clock)
    terms = parse_terms(texts)
    cutoff = max(clocks)
    if m.get("terms") != terms or datetime.fromisoformat(m["source_information_cutoff"]) != cutoff:
        raise ValueError("RESTRUCTURING_DECLARED_TERMS")
    return {**terms, "acquired_at": cutoff, "manifest_sha256": expected}


def qualified_exceptions(table: Any) -> Any:
    """Remove exactly one proved non-entitlement from a new effective view."""
    import pyarrow as pa
    import pyarrow.compute as pc

    selected = table.filter(pc.equal(table["exception_id"], EXCEPTION))
    if len(selected) != 1:
        raise ValueError("RESTRUCTURING_EXCEPTION_IDENTITY")
    r = selected.to_pylist()[0]
    if (
        r["security_id"] != "000792"
        or r["action_id"] != ACTION
        or r["effective_date"] is not None
        or str(r["blocked_from"]) != "2020-03-25"
        or r["blocked_through"] is not None
        or r["reason_code"] != "missing_effective_date"
        or r["authority_evidence_digests"] != []
    ):
        raise ValueError("RESTRUCTURING_EXCEPTION_BEFORE_IMAGE")
    return table.filter(pc.invert(pc.equal(table["exception_id"], pa.scalar(EXCEPTION))))


def rewrite_resume_limits(
    original: Path, target: Path, cutoff: datetime, *, verify_only: bool = False
) -> dict[str, int]:
    from datetime import date

    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not verify_only and (
        target.exists() or target.is_symlink() or target.resolve() == original.resolve()
    ):
        raise ValueError("RESTRUCTURING_LIMIT_OUTPUT_EXISTS")
    source = pq.ParquetFile(original)
    other = pq.ParquetFile(target) if verify_only else None
    if other and (
        other.schema_arrow != source.schema_arrow
        or other.metadata.num_rows != source.metadata.num_rows
    ):
        raise ValueError("RESTRUCTURING_LIMIT_SCHEMA")
    batches = iter(other.iter_batches(batch_size=65536)) if other else None
    writer = (
        None if verify_only else pq.ParquetWriter(target, source.schema_arrow, compression="zstd")
    )
    total = changed = 0
    try:
        for batch in source.iter_batches(batch_size=65536):
            table = pa.Table.from_batches([batch])
            mask = pc.and_(
                pc.equal(table["security_id"], "000792"),
                pc.equal(table["execution_date"], date(2021, 8, 10)),
            )
            selected = table.filter(mask)
            if len(selected):
                if (
                    len(selected) != 1
                    or selected["is_executable"][0].as_py() is not True
                    or selected["observed_tradestatus"][0].as_py() != "1"
                ):
                    raise ValueError("RESTRUCTURING_RESUME_BEFORE_IMAGE")
                for name in sorted(LIMIT_FIELDS):
                    field = table.schema.field(name)
                    value: Any = (
                        cutoff
                        if name == "knowledge_cutoff"
                        else VERSION
                        if name == "revision"
                        else None
                    )
                    table = table.set_column(
                        table.schema.get_field_index(name),
                        field,
                        pc.if_else(mask, pa.scalar(value, type=field.type), table[name]),
                    )
                changed += 1
            if batches is not None:
                actual = next(batches, None)
                if actual is None or not table.equals(
                    pa.Table.from_batches([actual]), check_metadata=True
                ):
                    raise ValueError("RESTRUCTURING_UNAUTHORIZED_LIMIT_CHANGE")
            else:
                assert writer is not None
                writer.write_table(table)
            total += len(table)
    finally:
        if writer:
            writer.close()
    if changed != 1 or (batches is not None and next(batches, None) is not None):
        raise ValueError("RESTRUCTURING_RESUME_KEY_CENSUS")
    return {"rows": total, "changed_keys": changed, "unchanged_rows": total - changed}


def materialize(
    parent_root: Path, source: Path, expected_source: str, output: Path
) -> dict[str, Any]:
    import pyarrow.parquet as pq

    if (
        output.exists()
        or output.is_symlink()
        or output.resolve() != output.absolute()
        or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (parent_root, source))
    ):
        raise ValueError("RESTRUCTURING_INPUT_ISOLATION")
    base, _ = parent.load_input(parent_root, PARENT_SHA)
    fact = load_source(source, expected_source)
    before = base.historical_bundle.partition_paths("corporate_action_exceptions")[0]
    table = pq.read_table(before)
    kept = qualified_exceptions(table)
    output.mkdir()
    shutil.copytree(source, output / "official", copy_function=shutil.copy2)
    (output / "effective").mkdir()
    pq.write_table(kept, output / "effective/exceptions.parquet", compression="zstd")
    stats = rewrite_resume_limits(
        base.historical_bundle.partition_paths("price_limit_execution_daily")[0],
        output / "effective/limits.parquet",
        fact["acquired_at"],
    )
    manifest = {
        "kind": VERSION,
        "parent_manifest_sha256": PARENT_SHA,
        "qualified_source_sha256": expected_source,
        "source_information_cutoff": fact["acquired_at"].isoformat(),
        "parent_exception_member": before.relative_to(parent_root).as_posix(),
        "exception_id": EXCEPTION,
        "exceptions_before": len(table),
        "exceptions_after": len(kept),
        "resume_stats": stats,
        "historical_PIT_claim": False,
        "production_authorized": False,
        "parent_materialization": "EXPLICIT_IMMUTABLE_ROLE_OWNED_PARENT_NOT_COPIED",
        "portable_publication": False,
        "producer_code_sha256": sha(Path(__file__)),
        "files": [
            {"path": p.relative_to(output).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)}
            for p in physical._files(output)
        ],
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (output / "manifest.json").write_bytes(raw)
    identity = hashlib.sha256(raw).hexdigest()
    (output / "SOURCE_READY").write_text(identity + "\n")
    return {
        "manifest_sha256": identity,
        "exceptions_before": len(table),
        "exceptions_after": len(kept),
        "resume_stats": stats,
    }


def load_input(root: Path, expected: str, parent_root: Path) -> Any:
    import pyarrow.parquet as pq

    paths = physical._files(root)
    if (
        sha(root / "manifest.json") != expected
        or (root / "SOURCE_READY").read_text() != expected + "\n"
    ):
        raise ValueError("RESTRUCTURING_INPUT_IDENTITY")
    m = json.loads((root / "manifest.json").read_bytes())
    if (
        m.get("kind") != VERSION
        or m.get("parent_manifest_sha256") != PARENT_SHA
        or m.get("exception_id") != EXCEPTION
        or any(
            m.get(k) is not False
            for k in ("portable_publication", "historical_PIT_claim", "production_authorized")
        )
        or m.get("parent_materialization") != "EXPLICIT_IMMUTABLE_ROLE_OWNED_PARENT_NOT_COPIED"
    ):
        raise ValueError("RESTRUCTURING_INPUT_SCOPE")
    declared = {"manifest.json", "SOURCE_READY"}
    for row in m["files"]:
        name = row["path"]
        if name in declared or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("RESTRUCTURING_INPUT_MEMBER")
        declared.add(name)
        if (root / name).stat().st_size != row["bytes"] or sha(root / name) != row["sha256"]:
            raise ValueError("RESTRUCTURING_INPUT_BYTES")
    if {p.relative_to(root).as_posix() for p in paths} != declared:
        raise ValueError("RESTRUCTURING_INPUT_PHYSICAL_SET")
    base, facts = parent.load_input(parent_root, PARENT_SHA)
    fact = load_source(root / "official", m["qualified_source_sha256"])
    before = base.historical_bundle.partition_paths("corporate_action_exceptions")[0]
    table = pq.read_table(before)
    expected_exceptions = qualified_exceptions(table)
    if (
        before.relative_to(parent_root).as_posix() != m["parent_exception_member"]
        or not expected_exceptions.equals(
            pq.read_table(root / "effective/exceptions.parquet"), check_metadata=True
        )
        or len(table) != m["exceptions_before"]
        or len(expected_exceptions) != m["exceptions_after"]
    ):
        raise ValueError("RESTRUCTURING_EXCEPTION_RECOMPUTED_CLOSURE")
    cutoff = fact["acquired_at"]
    if (
        datetime.fromisoformat(m["source_information_cutoff"]) != cutoff
        or cutoff < base.historical_bundle.snapshot.knowledge_cutoff
        or rewrite_resume_limits(
            base.historical_bundle.partition_paths("price_limit_execution_daily")[0],
            root / "effective/limits.parquet",
            cutoff,
            verify_only=True,
        )
        != m["resume_stats"]
    ):
        raise ValueError("RESTRUCTURING_RECOMPUTED_CLOSURE")
    replacements = {
        "corporate_action_exceptions": root / "effective/exceptions.parquet",
        "price_limit_execution_daily": root / "effective/limits.parquet",
    }
    datasets = []
    for old in base.historical_bundle.datasets:
        files = (
            (replacements[old.dataset],)
            if old.dataset in replacements
            else base.historical_bundle.partition_paths(old.dataset)
        )
        # Absolute paths are explicit role-local dependencies, never unverified
        # fallback. The two cold/primary parents have independent physical inodes.
        if any(
            p.resolve() != p or not any(p.is_relative_to(r) for r in (root, parent_root))
            for p in files
        ):
            raise ValueError("RESTRUCTURING_PARENT_PATH_ESCAPE")
        datasets.append(
            replace(
                old,
                partitions=tuple(str(p) for p in files),
                content_checksums={str(p): "sha256:" + sha(p) for p in files},
                row_count=len(expected_exceptions)
                if old.dataset == "corporate_action_exceptions"
                else old.row_count,
                knowledge_cutoff=cutoff if old.dataset in replacements else old.knowledge_cutoff,
                parent_manifest_ids=(old.manifest_id,),
            )
        )
    historical = replace(
        base.historical_bundle,
        root=root,
        datasets=tuple(datasets),
        canonical_identity_digest="sha256:" + expected,
        snapshot=SnapshotRef(
            base.historical_bundle.snapshot.market_date, cutoff, VERSION + ":" + expected, root.name
        ),
    )
    bundle = replace(
        base,
        root=root,
        bundle_id=root.name,
        manifest_sha256="sha256:" + expected,
        bundle_content_digest="sha256:" + expected,
        historical_bundle=historical,
        profile_parts={
            k: tuple(str(parent_root / p) for p in v) for k, v in base.profile_parts.items()
        },
    )
    return bundle, facts
