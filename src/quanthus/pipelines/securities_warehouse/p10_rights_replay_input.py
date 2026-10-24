"""Bounded, portable input revision for one qualified optional-rights reference.

This is an explicit retrospective successor contract. It is never loaded as the
legacy ADS envelope, a warehouse CURRENT, or production input. Old rows and all
evidence are retained, while a native Parquet execution view changes one key.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import load_corporate_action_normalization_policy
from quanthus.strategy_replay_ads import (
    StrategyReplayADSBundle,
    load_strategy_replay_ads,
    load_strategy_replay_ads_policy,
)

from .p10_rights_issue_reference import load_official_rule
from .p10_rights_issue_source import _digest, load_rights_source

VERSION = "p10-optional-rights-retrospective-input-v1"
PARENT = "strategy-replay-development-1f1ea4d-v1"
PARENT_SHA = "9d6af65970d496658946e8d5739a31c403c12cf70845cd7e16863e44293cd628"
ISSUER_SHA = "281557ec2b0a49b5f85ddfe99b0330701bd9ae31dc5d40bcb8d299bf999f7ced"
RULE_SHA = "17640bf7c32cabbb90f12f802e21975ea4634502f31dd2ba959f3660e38dfa32"
PRODUCT_SHA = "ef3412d5f5bfb1148a3b2b524b6f2ae5ec89e05b813d79b7bbb0485c4fa748b0"
REPLACED = {
    "price_reference",
    "price_reference_kind",
    "lower_limit_price",
    "upper_limit_price",
    "knowledge_cutoff",
    "revision",
}
EXACT_COLUMNS = ["reference_exact_numerator", "reference_exact_denominator"]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path) -> list[Path]:
    if root.is_symlink() or root.resolve() != root.absolute() or not root.is_dir():
        raise ValueError("RIGHTS_INPUT_PHYSICAL_ROOT")
    result = []
    for directory, dirs, files in os.walk(root, onerror=lambda exc: _raise(exc)):
        for name in dirs + files:
            p = Path(directory) / name
            if p.is_symlink():
                raise ValueError("RIGHTS_INPUT_SYMLINK")
            if name in files:
                if not p.is_file():
                    raise ValueError("RIGHTS_INPUT_NOT_ORDINARY")
                result.append(p)
    return sorted(result)


def _raise(exc: OSError) -> None:
    raise exc


def _read_reference(root: Path) -> dict[str, Any]:
    if _sha(root / "manifest.json") != PRODUCT_SHA:
        raise ValueError("RIGHTS_INPUT_REFERENCE_IDENTITY")
    m = json.loads((root / "manifest.json").read_bytes())
    if m["status"] != "EXACT_REFERENCE_QUALIFIED":
        raise ValueError("RIGHTS_INPUT_REFERENCE_UNQUALIFIED")
    declared = {"manifest.json", "SHA256SUMS"}
    for row in m["files"]:
        name = row["path"]
        if Path(name).name != name or name in declared:
            raise ValueError("RIGHTS_INPUT_REFERENCE_MEMBER")
        declared.add(name)
        if _sha(root / name) != row["sha256"]:
            raise ValueError("RIGHTS_INPUT_REFERENCE_MEMBER")
    if {p.name for p in _files(root)} != declared:
        raise ValueError("RIGHTS_INPUT_REFERENCE_SET")
    return json.loads((root / "derived-reference.json").read_bytes())


def _replacements(reference: dict[str, Any]) -> tuple[Fraction, dict[str, Any]]:
    exact = Fraction(int(reference["reference_numerator"]), int(reference["reference_denominator"]))
    with localcontext() as ctx:
        ctx.prec = 50
        projection = (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(
            Decimal("0.0001"), rounding="ROUND_HALF_UP"
        )
    return exact, {
        "price_reference": projection,
        "price_reference_kind": "exact_rights_rational_with_4dp_compatibility_projection",
        "lower_limit_price": Decimal(reference["lower_limit_price"]),
        "upper_limit_price": Decimal(reference["upper_limit_price"]),
        "knowledge_cutoff": datetime.fromisoformat(reference["source_information_cutoff"]).date(),
        "revision": VERSION + ":" + PRODUCT_SHA,
    }


def verify_limits(source: Path, target: Path, reference: dict[str, Any]) -> dict[str, int]:
    """Recompute the entire permitted difference, including a fully resealed copy."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    original, output = pq.ParquetFile(source), pq.ParquetFile(target)
    expected_schema = original.schema_arrow
    for name in EXACT_COLUMNS:
        expected_schema = expected_schema.append(pa.field(name, pa.string()))
    if not output.schema_arrow.equals(expected_schema, check_metadata=True):
        raise ValueError("RIGHTS_INPUT_SCHEMA")
    exact, replacements = _replacements(reference)
    checked, changed = 0, 0
    for before, after in zip(
        original.iter_batches(batch_size=65536), output.iter_batches(batch_size=65536), strict=True
    ):
        mask = pc.and_(
            pc.equal(before.column("security_id"), "000901"),
            pc.equal(before.column("execution_date"), pa.scalar(date(2014, 10, 24))),
        )
        changed += pc.sum(pc.cast(mask, pa.int64())).as_py()
        for name in before.schema.names:
            left, right = before.column(name), after.column(name)
            if name in REPLACED:
                left = pc.if_else(mask, pa.scalar(replacements[name], type=left.type), left)
            if not left.equals(right):
                raise ValueError("RIGHTS_INPUT_UNAPPROVED_VALUE_CHANGE")
        for name, value in zip(
            EXACT_COLUMNS, (str(exact.numerator), str(exact.denominator)), strict=True
        ):
            if not after.column(name).equals(
                pc.if_else(mask, pa.scalar(value), pa.scalar(None, pa.string()))
            ):
                raise ValueError("RIGHTS_INPUT_RATIONAL_CLOSURE")
        checked += len(before)
    if (
        output.metadata.num_rows != original.metadata.num_rows
        or checked != original.metadata.num_rows
        or changed != 1
    ):
        raise ValueError("RIGHTS_INPUT_ROW_CLOSURE")
    return {
        "rows": checked,
        "changed_keys": changed,
        "all_other_original_values_checked": checked - changed,
    }


def rewrite_limits(source: Path, target: Path, reference: dict[str, Any]) -> dict[str, int]:
    """Scan/rewrite in native batches; preserve every other value and original key."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if target.exists() or target.is_symlink():
        raise ValueError("RIGHTS_INPUT_OUTPUT_EXISTS")
    if reference["security_code"] != "000901" or reference["execution_date"] != "2014-10-24":
        raise ValueError("RIGHTS_INPUT_EXACT_KEY")
    original = pq.ParquetFile(source)
    if any(name in original.schema_arrow.names for name in EXACT_COLUMNS):
        raise ValueError("RIGHTS_INPUT_ALREADY_REWRITTEN")
    schema = original.schema_arrow
    for name in EXACT_COLUMNS:
        schema = schema.append(pa.field(name, pa.string()))
    exact, replacements = _replacements(reference)
    count, changed = 0, 0
    with pq.ParquetWriter(target, schema, compression="zstd") as writer:
        for batch in original.iter_batches(batch_size=65536):
            mask = pc.and_(
                pc.equal(batch.column("security_id"), "000901"),
                pc.equal(batch.column("execution_date"), pa.scalar(date(2014, 10, 24))),
            )
            n = pc.sum(pc.cast(mask, pa.int64())).as_py()
            table = pa.Table.from_batches([batch])
            if n:
                for name, value in replacements.items():
                    i = table.schema.get_field_index(name)
                    table = table.set_column(
                        i,
                        table.schema.field(i),
                        pc.if_else(
                            mask, pa.scalar(value, type=table.schema.field(i).type), table[name]
                        ),
                    )
            for name, value in zip(
                EXACT_COLUMNS, (str(exact.numerator), str(exact.denominator)), strict=True
            ):
                table = table.append_column(
                    name, pc.if_else(mask, pa.scalar(value), pa.scalar(None, pa.string()))
                )
            writer.write_table(table)
            count += len(batch)
            changed += n
    if changed != 1:
        raise ValueError("RIGHTS_INPUT_REPLACEMENT_KEY_COUNT")
    result = verify_limits(source, target, reference)
    if result["rows"] != count:
        raise ValueError("RIGHTS_INPUT_ROW_CLOSURE")
    return result


def materialize(
    parent: Path, issuer: Path, rule: Path, product: Path, target: Path
) -> dict[str, Any]:
    """Create independent files only, with closure and SOURCE_READY written last."""
    inputs = [p.resolve() for p in (parent, issuer, rule, product)]
    destination = target.absolute()
    if (
        destination.resolve() != destination
        or any(
            destination == p or destination.is_relative_to(p) or p.is_relative_to(destination)
            for p in inputs
        )
        or target.exists()
    ):
        raise ValueError("RIGHTS_INPUT_OUTPUT_ISOLATION")
    for p in (parent, issuer, rule, product):
        _files(p)
    if _sha(parent / "manifest.json") != PARENT_SHA:
        raise ValueError("RIGHTS_INPUT_PARENT_IDENTITY")
    load_strategy_replay_ads(
        parent,
        ads_policy=load_strategy_replay_ads_policy(),
        reader_role="development_research",
        corporate_action_policy=load_corporate_action_normalization_policy(),
    )
    load_rights_source(issuer, ISSUER_SHA)
    load_official_rule(rule, RULE_SHA)
    reference = _read_reference(product)
    target.mkdir()
    for src, rel in (
        (parent, "parent/" + PARENT),
        (issuer, "evidence/issuer"),
        (rule, "evidence/rule"),
        (product, "evidence/reference"),
    ):
        shutil.copytree(src, target / rel, copy_function=shutil.copy2)
    # Verify the newly copied parent, not an old receipt or the copy syscall.
    load_strategy_replay_ads(
        target / "parent" / PARENT,
        ads_policy=load_strategy_replay_ads_policy(),
        reader_role="development_research",
        corporate_action_policy=load_corporate_action_normalization_policy(),
    )
    (target / "effective").mkdir()
    stats = rewrite_limits(
        target / "parent" / PARENT / "tables/price_limit_execution_daily.parquet",
        target / "effective/price_limits.parquet",
        reference,
    )
    files = [
        {"path": p.relative_to(target).as_posix(), "bytes": p.stat().st_size, "sha256": _sha(p)}
        for p in _files(target)
    ]
    manifest = {
        "kind": VERSION,
        "contract_kind": "explicit_retrospective_successor_not_legacy_ADS",
        "parent_manifest_sha256": PARENT_SHA,
        "issuer_manifest_sha256": ISSUER_SHA,
        "rule_manifest_sha256": RULE_SHA,
        "reference_manifest_sha256": PRODUCT_SHA,
        "source_information_cutoff": reference["source_information_cutoff"],
        "source_scope": ["2012-01-04", "2021-12-30", "2021-12-31"],
        "single_key": ["000901", "2014-10-24"],
        "allowed_original_columns": sorted(REPLACED),
        "exact_reference_columns": EXACT_COLUMNS,
        "price_reference_compatibility_projection_is_not_canonical": True,
        "whole_history_quality_admission": False,
        "production_authorized": False,
        "original_source_files_modified": False,
        "stats": stats,
        "files": files,
        "producer_code_sha256": _sha(Path(__file__)),
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (target / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (target / "SOURCE_READY").write_text(digest + "\n")
    return {"manifest_sha256": digest, "bytes": sum(f["bytes"] for f in files), **stats}


def load_input(root: Path, expected_manifest_sha256: str) -> StrategyReplayADSBundle:
    """Adapt the explicitly admitted successor to the unchanged frame interface."""
    import pyarrow.parquet as pq

    expected = _digest(expected_manifest_sha256)
    paths = _files(root)
    if (
        _sha(root / "manifest.json") != expected
        or (root / "SOURCE_READY").read_text() != expected + "\n"
    ):
        raise ValueError("RIGHTS_INPUT_MANIFEST_OR_READY")
    m = json.loads((root / "manifest.json").read_bytes())
    if (
        m.get("kind") != VERSION
        or m.get("contract_kind") != "explicit_retrospective_successor_not_legacy_ADS"
        or m.get("parent_manifest_sha256") != PARENT_SHA
        or m.get("issuer_manifest_sha256") != ISSUER_SHA
        or m.get("rule_manifest_sha256") != RULE_SHA
        or m.get("reference_manifest_sha256") != PRODUCT_SHA
        or m.get("single_key") != ["000901", "2014-10-24"]
        or m.get("source_scope") != ["2012-01-04", "2021-12-30", "2021-12-31"]
        or m.get("whole_history_quality_admission") is not False
        or m.get("production_authorized") is not False
    ):
        raise ValueError("RIGHTS_INPUT_SCOPE")
    declared = {"manifest.json", "SOURCE_READY"}
    for row in m["files"]:
        name = row["path"]
        p = root / name
        if name in declared or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("RIGHTS_INPUT_MEMBER_PATH")
        declared.add(name)
        if p.stat().st_size != row["bytes"] or _sha(p) != row["sha256"]:
            raise ValueError("RIGHTS_INPUT_MEMBER_CHANGED")
    if {p.relative_to(root).as_posix() for p in paths} != declared:
        raise ValueError("RIGHTS_INPUT_PHYSICAL_SET")
    facts = load_rights_source(root / "evidence/issuer", ISSUER_SHA)
    rule = load_official_rule(root / "evidence/rule", RULE_SHA)
    reference = _read_reference(root / "evidence/reference")
    if datetime.fromisoformat(m["source_information_cutoff"]) != max(
        facts.source_information_cutoff, rule.source_information_cutoff
    ):
        raise ValueError("RIGHTS_INPUT_CLOCK")
    parent = load_strategy_replay_ads(
        root / "parent" / PARENT,
        ads_policy=load_strategy_replay_ads_policy(),
        reader_role="development_research",
        corporate_action_policy=load_corporate_action_normalization_policy(),
    )
    changed = root / "effective/price_limits.parquet"
    if (
        verify_limits(
            parent.historical_bundle.partition_paths("price_limit_execution_daily")[0],
            changed,
            reference,
        )
        != m["stats"]
    ):
        raise ValueError("RIGHTS_INPUT_RECOMPUTED_CENSUS")
    parts = []
    for old in parent.historical_bundle.datasets:
        if old.dataset == "price_limit_execution_daily":
            parts.append(
                replace(
                    old,
                    version=VERSION,
                    partitions=("effective/price_limits.parquet",),
                    content_checksums={"effective/price_limits.parquet": "sha256:" + _sha(changed)},
                    schema_hash="sha256:"
                    + hashlib.sha256(pq.ParquetFile(changed).schema_arrow.serialize()).hexdigest(),
                    source_revisions={
                        **old.source_revisions,
                        "qualified_rights_reference": PRODUCT_SHA,
                        "qualified_exchange_rule": RULE_SHA,
                    },
                    knowledge_cutoff=datetime.fromisoformat(reference["source_information_cutoff"]),
                    parent_manifest_ids=(old.manifest_id,),
                )
            )
        else:
            parts.append(
                replace(
                    old,
                    partitions=tuple("parent/" + PARENT + "/" + p for p in old.partitions),
                    content_checksums={
                        "parent/" + PARENT + "/" + k: v for k, v in old.content_checksums.items()
                    },
                    parent_manifest_ids=(old.manifest_id,),
                )
            )
    historical = replace(
        parent.historical_bundle,
        root=root,
        datasets=tuple(parts),
        canonical_identity_digest="sha256:" + expected,
        snapshot=SnapshotRef(
            parent.historical_bundle.snapshot.market_date,
            datetime.fromisoformat(m["source_information_cutoff"]),
            VERSION + ":" + expected,
            root.name,
        ),
    )
    return replace(
        parent,
        root=root,
        bundle_id=root.name,
        manifest_sha256="sha256:" + expected,
        bundle_content_digest="sha256:" + expected,
        historical_bundle=historical,
        profile_parts={
            k: tuple("parent/" + PARENT + "/" + p for p in v)
            for k, v in parent.profile_parts.items()
        },
    )
