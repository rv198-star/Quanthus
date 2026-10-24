"""Explicit retrospective cash/bonus supplement to the retained rights input.

No legacy schema or historical knowledge timestamp is manufactured. The ordinary
P2 cash/bonus model is a governed replay assumption, not independently certified
exchange rules for 2012. Old rows and complete source bodies stay in the image.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

from quanthus.contracts import SnapshotRef

from . import p10_rights_replay_input as parent
from .p10_historical_action_source import ActionFacts, load_source, sha

VERSION = "p10-historical-cash-bonus-retrospective-input-v1"
PARENT_SHA = "59e3cc21e00e56c087e3a8d1f2ff1c413fad8b1eaceb3caf3c4e93d1c9f740d7"
MODEL = "existing-P2-gross-cash-bonus-theoretical-reference-v1"
ALLOWED = parent.REPLACED | set(parent.EXACT_COLUMNS)


def replacements(row: dict[str, Any], fact: ActionFacts) -> dict[str, Any]:
    """Retain the existing regime; no reference fitting from observed returns."""
    prior = row["price_reference"]
    if (
        not isinstance(prior, Decimal)
        or not prior.is_finite()
        or row["price_reference_kind"] != "prior_trading_close"
        or row["upper_limit_pct"] is None
        or row["lower_limit_pct"] is None
    ):
        raise ValueError("ACTION_INPUT_REFERENCE_UNQUALIFIED")
    exact = (Fraction(prior) - Fraction(fact.cash_per_share)) / (
        1 + Fraction(fact.bonus_share_ratio)
    )
    if exact <= 0:
        raise ValueError("ACTION_INPUT_NONPOSITIVE_REFERENCE")
    with localcontext() as ctx:
        ctx.prec = 64
        reference = Decimal(exact.numerator) / Decimal(exact.denominator)
        up = (reference * (1 + Decimal(row["upper_limit_pct"]) / 100)).quantize(
            Decimal("0.01"), rounding="ROUND_HALF_UP"
        )
        low = (reference * (1 - Decimal(row["lower_limit_pct"]) / 100)).quantize(
            Decimal("0.01"), rounding="ROUND_HALF_UP"
        )
        return {
            "price_reference": reference.quantize(Decimal("0.0001"), rounding="ROUND_HALF_UP"),
            "price_reference_kind": MODEL,
            "upper_limit_price": max(Decimal("0.01"), up),
            "lower_limit_price": max(Decimal("0.01"), low),
            "knowledge_cutoff": fact.acquired_at,
            "revision": fact.source_revision,
            "reference_exact_numerator": str(exact.numerator),
            "reference_exact_denominator": str(exact.denominator),
        }


def rewrite_limits(
    source: Path, target: Path, facts: tuple[ActionFacts, ...], *, verify_only: bool = False
) -> dict[str, int]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not verify_only and (
        target.exists() or target.is_symlink() or target.resolve() == source.resolve()
    ):
        raise ValueError("ACTION_INPUT_OUTPUT_EXISTS")
    original = pq.ParquetFile(source)
    schema = original.schema_arrow
    if not set(schema.names) >= ALLOWED:
        raise ValueError("ACTION_INPUT_SCHEMA")
    expected = {(f.security_code, f.ex_date): f for f in facts}
    if len(expected) != len(facts) or not expected:
        raise ValueError("ACTION_INPUT_DUPLICATE_KEY")
    found = {key: 0 for key in expected}
    writer = None if verify_only else pq.ParquetWriter(target, schema, compression="zstd")
    compared = pq.ParquetFile(target) if verify_only else None
    if compared is not None and (
        compared.schema_arrow != schema or compared.metadata.num_rows != original.metadata.num_rows
    ):
        raise ValueError("ACTION_INPUT_SCHEMA_OR_COUNT")
    other = iter(compared.iter_batches(batch_size=65536)) if compared else None
    total = 0
    try:
        for batch in original.iter_batches(batch_size=65536):
            table = pa.Table.from_batches([batch])
            for key, fact in expected.items():
                mask = pc.and_(
                    pc.equal(table["security_id"], key[0]),
                    pc.equal(table["execution_date"], pa.scalar(key[1])),
                )
                matches = pc.indices_nonzero(mask).to_pylist()
                if not matches:
                    continue
                found[key] += len(matches)
                if len(matches) != 1:
                    raise ValueError("ACTION_INPUT_KEY_COUNT")
                row = table.slice(matches[0], 1).to_pylist()[0]
                values = replacements(row, fact)
                for name, value in values.items():
                    i = schema.get_field_index(name)
                    table = table.set_column(
                        i,
                        schema.field(i),
                        pc.if_else(mask, pa.scalar(value, type=schema.field(i).type), table[name]),
                    )
            if other is not None:
                actual = pa.Table.from_batches([next(other)])
                if not table.equals(actual, check_metadata=True):
                    raise ValueError("ACTION_INPUT_UNAPPROVED_VALUE_CHANGE")
            else:
                assert writer is not None
                writer.write_table(table)
            total += len(table)
    finally:
        if writer is not None:
            writer.close()
    if any(n != 1 for n in found.values()):
        raise ValueError("ACTION_INPUT_KEY_COUNT")
    if other is not None and next(other, None) is not None:
        raise ValueError("ACTION_INPUT_EXTRA_ROWS")
    result = {"rows": total, "changed_keys": len(found), "unchanged_rows": total - len(found)}
    if not verify_only and rewrite_limits(source, target, facts, verify_only=True) != result:
        raise ValueError("ACTION_INPUT_RECOMPUTED_CENSUS")
    return result


def materialize(
    parent_root: Path, official: Path, official_sha: str, target: Path
) -> dict[str, Any]:
    roots = [parent_root, official]
    if (
        target.exists()
        or target.is_symlink()
        or target.resolve() != target.absolute()
        or any(target == p or target.is_relative_to(p) or p.is_relative_to(target) for p in roots)
    ):
        raise ValueError("ACTION_INPUT_ISOLATION")
    base = parent.load_input(parent_root, PARENT_SHA)
    facts = load_source(official, official_sha)
    if any(
        f.cash_payment_date != f.ex_date
        or (
            f.bonus_share_ratio
            and (f.share_credit_date != f.ex_date or f.share_listing_date != f.ex_date)
        )
        for f in facts
    ):
        raise ValueError("ACTION_INPUT_DELAYED_CREDIT_UNSUPPORTED")
    target.mkdir()
    shutil.copytree(parent_root, target / "parent", copy_function=shutil.copy2)
    shutil.copytree(official, target / "official", copy_function=shutil.copy2)
    copied = parent.load_input(target / "parent", PARENT_SHA)
    (target / "effective").mkdir()
    stats = rewrite_limits(
        copied.historical_bundle.partition_paths("price_limit_execution_daily")[0],
        target / "effective/price_limits.parquet",
        facts,
    )
    files = [
        {"path": p.relative_to(target).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)}
        for p in parent._files(target)
    ]
    m = {
        "kind": VERSION,
        "parent_manifest_sha256": PARENT_SHA,
        "official_manifest_sha256": official_sha,
        "source_information_cutoff": max(f.acquired_at for f in facts).isoformat(),
        "keys": [[f.security_code, f.ex_date.isoformat()] for f in facts],
        "source_scope": ["2012-01-04", "2021-12-30", "2021-12-31"],
        "reference_model": MODEL,
        "exchange_historical_rule_certification": False,
        "whole_history_quality_admission": False,
        "historical_PIT_claim": False,
        "production_authorized": False,
        "allowed_limit_columns": sorted(ALLOWED),
        "stats": stats,
        "files": files,
        "producer_code_sha256": sha(Path(__file__)),
    }
    raw = (json.dumps(m, sort_keys=True, indent=2) + "\n").encode()
    (target / "manifest.json").write_bytes(raw)
    identity = hashlib.sha256(raw).hexdigest()
    (target / "SOURCE_READY").write_text(identity + "\n")
    return {
        "manifest_sha256": identity,
        "official_manifest_sha256": official_sha,
        "stats": stats,
        "bytes": sum(r["bytes"] for r in files),
        "parent_source_unchanged": base.manifest_sha256 == copied.manifest_sha256,
    }


def load_input(root: Path, expected: str) -> tuple[Any, tuple[ActionFacts, ...]]:
    """Load only this explicit contract, never call the legacy source on its root."""
    import pyarrow.parquet as pq

    paths = parent._files(root)
    if (
        sha(root / "manifest.json") != expected
        or (root / "SOURCE_READY").read_text() != expected + "\n"
    ):
        raise ValueError("ACTION_INPUT_IDENTITY")
    m = json.loads((root / "manifest.json").read_bytes())
    if (
        m.get("kind") != VERSION
        or m.get("parent_manifest_sha256") != PARENT_SHA
        or m.get("reference_model") != MODEL
        or m.get("source_scope") != ["2012-01-04", "2021-12-30", "2021-12-31"]
        or m.get("allowed_limit_columns") != sorted(ALLOWED)
        or any(
            m.get(k) is not False
            for k in (
                "historical_PIT_claim",
                "exchange_historical_rule_certification",
                "whole_history_quality_admission",
                "production_authorized",
            )
        )
    ):
        raise ValueError("ACTION_INPUT_SCOPE")
    declared = {"manifest.json", "SOURCE_READY"}
    for row in m["files"]:
        name = row["path"]
        if name in declared or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("ACTION_INPUT_MEMBER")
        declared.add(name)
        p = root / name
        if p.stat().st_size != row["bytes"] or sha(p) != row["sha256"]:
            raise ValueError("ACTION_INPUT_MEMBER_CHANGED")
    if {p.relative_to(root).as_posix() for p in paths} != declared:
        raise ValueError("ACTION_INPUT_PHYSICAL_SET")
    base = parent.load_input(root / "parent", PARENT_SHA)
    facts = load_source(root / "official", m["official_manifest_sha256"])
    if (
        m["keys"] != [[f.security_code, f.ex_date.isoformat()] for f in facts]
        or datetime.fromisoformat(m["source_information_cutoff"])
        != max(f.acquired_at for f in facts)
        or rewrite_limits(
            base.historical_bundle.partition_paths("price_limit_execution_daily")[0],
            root / "effective/price_limits.parquet",
            facts,
            verify_only=True,
        )
        != m["stats"]
    ):
        raise ValueError("ACTION_INPUT_RECOMPUTED_CLOSURE")
    datasets = []
    for old in base.historical_bundle.datasets:
        if old.dataset == "price_limit_execution_daily":
            name = "effective/price_limits.parquet"
            part = replace(
                old,
                version=VERSION,
                partitions=(name,),
                content_checksums={name: "sha256:" + sha(root / name)},
                schema_hash="sha256:"
                + hashlib.sha256(pq.ParquetFile(root / name).schema_arrow.serialize()).hexdigest(),
                knowledge_cutoff=max(f.acquired_at for f in facts),
                parent_manifest_ids=(old.manifest_id,),
            )
        else:
            part = replace(
                old,
                partitions=tuple("parent/" + p for p in old.partitions),
                content_checksums={"parent/" + k: v for k, v in old.content_checksums.items()},
                parent_manifest_ids=(old.manifest_id,),
            )
        datasets.append(part)
    historical = replace(
        base.historical_bundle,
        root=root,
        datasets=tuple(datasets),
        canonical_identity_digest="sha256:" + expected,
        snapshot=SnapshotRef(
            base.historical_bundle.snapshot.market_date,
            max(f.acquired_at for f in facts),
            VERSION + ":" + expected,
            root.name,
        ),
    )
    return replace(
        base,
        root=root,
        bundle_id=root.name,
        manifest_sha256="sha256:" + expected,
        bundle_content_digest="sha256:" + expected,
        historical_bundle=historical,
        profile_parts={k: tuple("parent/" + p for p in v) for k, v in base.profile_parts.items()},
    ), facts
