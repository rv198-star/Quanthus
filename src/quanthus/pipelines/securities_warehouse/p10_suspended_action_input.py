"""Exact P10 #560 reference carry; raw bars, action dates and status stay intact.

The last traded close must carry the already qualified cash/bonus entitlement
once during a suspension. A zero-activity provider bar is corroboration, never
the source of a fitted reference. Original inputs remain inside this successor.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.contracts import SnapshotRef
from quanthus.expert_evaluation.serialization import digest_value

from . import p10_historical_action_input as rules
from . import p10_historical_action_source as official
from . import p10_rights_replay_input as physical
from . import p10_tch_held_action_input as parent

VERSION = "p10-002606-suspended-action-reference-input-v1"
PARENT_SHA = "44bcc661577e47c8376570cdc4c8a56897afebc95c6fcdfae2adbd8adb683e5b"
OFFICIAL_SHA = "cd882f100754b34e437eac2b0dfe0b1f20be283c29aac02b53475f32eb852ef3"
CODE = "002606"
EX = date(2017, 5, 12)


def _facts(root: Path, identity: str) -> Any:
    if identity != OFFICIAL_SHA:
        raise ValueError("SUSPENDED_SOURCE_IDENTITY")
    facts = official.load_source(root, identity)
    if len(facts) != 1 or any(
        f.security_code != CODE
        or f.ex_date != EX
        or f.record_date != date(2017, 5, 11)
        or any(d != EX for d in (f.cash_payment_date, f.share_credit_date, f.share_listing_date))
        for f in facts
    ):
        raise ValueError("SUSPENDED_SOURCE_TERMS")
    return facts[0]


def carry_plan(bars: list[Any], limits: list[Any], actions: list[Any], fact: Any) -> list[Any]:
    """Bound one action's dependency interval through the first actual session.

    A real tradable jump is never excused: its new bounds still feed the ordinary
    unexplained-transition guard. No return is used to select action terms.
    """
    if fact.security_code != CODE or fact.ex_date != EX:
        raise ValueError("SUSPENDED_EXACT_SCOPE")
    b = {r["date"]: r for r in bars}
    limits_by_date = {r["execution_date"]: r for r in limits}
    if (
        len(b) != len(bars)
        or len(limits_by_date) != len(limits)
        or any(r["security_id"] != CODE for r in [*bars, *limits, *actions])
    ):
        raise ValueError("SUSPENDED_DUPLICATE_OR_IDENTITY")
    matching = [r for r in actions if r["effective_date"] == EX]
    if len(matching) != 1 or (
        Decimal(str(matching[0]["cash_dividend_per_share"])) != fact.cash_per_share
        or Decimal(str(matching[0]["bonus_share_ratio"])) != fact.bonus_share_ratio
    ):
        raise ValueError("SUSPENDED_EXISTING_ACTION_CONFLICT")
    before = [d for d, r in limits_by_date.items() if d < EX and r["observed_tradestatus"] == "1"]
    resumed = [d for d, r in limits_by_date.items() if d > EX and r["observed_tradestatus"] == "1"]
    if not before or not resumed or EX not in b or EX not in limits_by_date:
        raise ValueError("SUSPENDED_INTERVAL_UNCLOSED")
    prior, end = max(before), min(resumed)
    if prior not in b or any(
        prior < r["effective_date"] <= end and r is not matching[0] for r in actions
    ):
        raise ValueError("SUSPENDED_OTHER_ACTION_OR_PRIOR")
    dates = sorted(d for d in limits_by_date if EX <= d <= end)
    if not dates or set(dates) != {d for d in b if EX <= d <= end}:
        raise ValueError("SUSPENDED_BAR_LIMIT_CLOSURE")
    prior_close = Decimal(str(b[prior]["close"]))
    if not prior_close.is_finite() or prior_close <= 0:
        raise ValueError("SUSPENDED_PRIOR_CLOSE")
    changed = []
    for day in dates:
        row, bar = limits_by_date[day], b[day]
        if (
            row["price_reference"] != prior_close
            or row["price_reference_kind"] != "prior_trading_close"
        ):
            raise ValueError("SUSPENDED_REFERENCE_BEFORE_IMAGE")
        value = rules.replacements(row, fact)
        if day < end:
            expected = (
                (prior_close - fact.cash_per_share) / (1 + fact.bonus_share_ratio)
            ).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
            if (
                row["observed_tradestatus"] != "0"
                or row["is_executable"] is not False
                or bar["volume"] != 0
                or bar["amount"] != 0
                or any(Decimal(str(bar[k])) != expected for k in ("open", "high", "low", "close"))
            ):
                raise ValueError("SUSPENDED_PLACEHOLDER_CONFLICT")
        changed.append({"security_id": CODE, "execution_date": day, **value})
    return changed


def _plan(bundle: Any, fact: Any) -> list[Any]:
    import duckdb

    connection = duckdb.connect(
        config={"threads": 2, "memory_limit": "2GB", "max_temp_directory_size": "0B"}
    )
    try:
        selected = []
        for dataset in (
            "daily_ohlcv_none",
            "price_limit_execution_daily",
            "corporate_actions_normalized",
        ):
            paths = [str(p) for p in bundle.historical_bundle.partition_paths(dataset)]
            cursor = connection.execute(
                "SELECT * FROM read_parquet(?) WHERE security_id=?", [paths, CODE]
            )
            names = [str(r[0]) for r in cursor.description]
            selected.append([dict(zip(names, r, strict=True)) for r in cursor.fetchall()])
        return carry_plan(selected[0], selected[1], selected[2], fact)
    finally:
        connection.close()


def rewrite_limits(
    source: Path, target: Path, plan: list[Any], *, verify_only: bool = False
) -> Any:
    """Stream every row and verify the exact allowed changes, including resealed tampering."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not plan or len({r["execution_date"] for r in plan}) != len(plan):
        raise ValueError("SUSPENDED_PLAN_KEYS")
    if not verify_only and (
        target.exists() or target.is_symlink() or target.resolve() == source.resolve()
    ):
        raise ValueError("SUSPENDED_OUTPUT_EXISTS")
    original = pq.ParquetFile(source)
    schema = original.schema_arrow
    patch_schema = pa.schema([schema.field(n) for n in ("execution_date", *sorted(rules.ALLOWED))])
    patch = pa.Table.from_pylist(plan, schema=patch_schema)
    compared = pq.ParquetFile(target) if verify_only else None
    if compared is not None and (
        not compared.schema_arrow.equals(schema, check_metadata=True)
        or compared.metadata.num_rows != original.metadata.num_rows
    ):
        raise ValueError("SUSPENDED_SCHEMA_OR_COUNT")
    other = iter(compared.iter_batches(batch_size=65536)) if compared else None
    writer = None if verify_only else pq.ParquetWriter(target, schema, compression="zstd")
    total = found = 0
    key_counts = {r["execution_date"]: 0 for r in plan}
    try:
        for batch in original.iter_batches(batch_size=65536):
            table = pa.Table.from_batches([batch])
            indexes = pc.index_in(
                table["execution_date"], value_set=patch["execution_date"].combine_chunks()
            )
            mask = pc.and_(pc.equal(table["security_id"], CODE), pc.is_valid(indexes))
            count = pc.sum(pc.cast(mask, pa.int64())).as_py()
            if count:
                for day in table.filter(mask)["execution_date"].to_pylist():
                    key_counts[day] += 1
                for name in sorted(rules.ALLOWED):
                    field = schema.field(name)
                    values = pc.take(patch[name], indexes)
                    table = table.set_column(
                        schema.get_field_index(name), field, pc.if_else(mask, values, table[name])
                    )
                found += count
            if other is not None:
                actual = next(other, None)
                if actual is None or not table.equals(
                    pa.Table.from_batches([actual]), check_metadata=True
                ):
                    raise ValueError("SUSPENDED_UNAPPROVED_VALUE_CHANGE")
            else:
                assert writer is not None
                writer.write_table(table)
            total += len(table)
    finally:
        if writer is not None:
            writer.close()
    if found != len(plan) or any(n != 1 for n in key_counts.values()):
        raise ValueError("SUSPENDED_KEY_CENSUS")
    if other is not None and next(other, None) is not None:
        raise ValueError("SUSPENDED_EXTRA_ROWS")
    return {
        "rows": total,
        "changed_keys": found,
        "unchanged_rows": total - found,
        "allowed_key_digest": digest_value([[CODE, r["execution_date"].isoformat()] for r in plan]),
        "reference_values_digest": digest_value(json.loads(json.dumps(plan, default=str))),
    }


def materialize(source: Path, official_root: Path, official_sha: str, target: Path) -> Any:
    if (
        target.exists()
        or target.is_symlink()
        or target.resolve() != target.absolute()
        or any(
            p.resolve() != p.absolute()
            or target == p
            or target.is_relative_to(p)
            or p.is_relative_to(target)
            for p in (source, official_root)
        )
    ):
        raise ValueError("SUSPENDED_INPUT_ISOLATION")
    base, _old = parent.load_input(source, PARENT_SHA)
    fact = _facts(official_root, official_sha)
    plan = _plan(base, fact)
    target.mkdir()
    shutil.copytree(source, target / "parent", copy_function=shutil.copy2)
    shutil.copytree(official_root, target / "official", copy_function=shutil.copy2)
    (target / "effective").mkdir()
    source_limits = base.historical_bundle.partition_paths("price_limit_execution_daily")[0]
    stats = rewrite_limits(source_limits, target / "effective/price_limits.parquet", plan)
    if (
        rewrite_limits(
            source_limits, target / "effective/price_limits.parquet", plan, verify_only=True
        )
        != stats
    ):
        raise ValueError("SUSPENDED_RECOMPUTED_CENSUS")
    files = [
        {
            "path": p.relative_to(target).as_posix(),
            "bytes": p.stat().st_size,
            "sha256": official.sha(p),
        }
        for p in physical._files(target)
    ]
    manifest = {
        "kind": VERSION,
        "parent_manifest_sha256": PARENT_SHA,
        "official_manifest_sha256": official_sha,
        "source_information_cutoff": fact.acquired_at.isoformat(),
        "scope": ["2012-01-04", "2021-12-30", "2021-12-31"],
        "reference_model": rules.MODEL,
        "action_date": EX.isoformat(),
        "carry_keys": [[CODE, r["execution_date"].isoformat()] for r in plan],
        "historical_PIT_claim": False,
        "production_authorized": False,
        "actual_share_credit_known": False,
        "producer_code_sha256": official.sha(Path(__file__)),
        "stats": stats,
        "files": files,
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (target / "manifest.json").write_bytes(raw)
    identity = hashlib.sha256(raw).hexdigest()
    (target / "SOURCE_READY").write_text(identity + "\n")
    return {
        "manifest_sha256": identity,
        "stats": stats,
        "first_carry_date": plan[0]["execution_date"].isoformat(),
        "last_carry_date": plan[-1]["execution_date"].isoformat(),
        "bytes": sum(r["bytes"] for r in files),
    }


def load_input(root: Path, expected: str) -> Any:
    import pyarrow.parquet as pq

    paths = physical._files(root)
    if (
        official.sha(root / "manifest.json") != expected
        or (root / "SOURCE_READY").read_text() != expected + "\n"
    ):
        raise ValueError("SUSPENDED_INPUT_IDENTITY")
    m = json.loads((root / "manifest.json").read_bytes())
    if (
        m.get("kind") != VERSION
        or m.get("parent_manifest_sha256") != PARENT_SHA
        or m.get("reference_model") != rules.MODEL
        or m.get("action_date") != EX.isoformat()
        or m.get("scope") != ["2012-01-04", "2021-12-30", "2021-12-31"]
        or any(
            m.get(k) is not False
            for k in ("historical_PIT_claim", "production_authorized", "actual_share_credit_known")
        )
    ):
        raise ValueError("SUSPENDED_INPUT_SCOPE")
    declared = {"manifest.json", "SOURCE_READY"}
    for r in m["files"]:
        n = r["path"]
        if n in declared or Path(n).is_absolute() or ".." in Path(n).parts:
            raise ValueError("SUSPENDED_INPUT_MEMBER")
        declared.add(n)
        p = root / n
        if p.stat().st_size != r["bytes"] or official.sha(p) != r["sha256"]:
            raise ValueError("SUSPENDED_INPUT_MEMBER_CHANGED")
    if {p.relative_to(root).as_posix() for p in paths} != declared:
        raise ValueError("SUSPENDED_INPUT_PHYSICAL_SET")
    base, facts = parent.load_input(root / "parent", PARENT_SHA)
    fact = _facts(root / "official", m["official_manifest_sha256"])
    plan = _plan(base, fact)
    cutoff = fact.acquired_at
    if (
        datetime.fromisoformat(m["source_information_cutoff"]) != cutoff
        or cutoff < base.historical_bundle.snapshot.knowledge_cutoff
        or m["carry_keys"] != [[CODE, r["execution_date"].isoformat()] for r in plan]
        or rewrite_limits(
            base.historical_bundle.partition_paths("price_limit_execution_daily")[0],
            root / "effective/price_limits.parquet",
            plan,
            verify_only=True,
        )
        != m["stats"]
    ):
        raise ValueError("SUSPENDED_INPUT_RECOMPUTED_CLOSURE")
    datasets = []
    for old in base.historical_bundle.datasets:
        if old.dataset == "price_limit_execution_daily":
            n = "effective/price_limits.parquet"
            dataset = replace(
                old,
                version=VERSION,
                partitions=(n,),
                content_checksums={n: "sha256:" + official.sha(root / n)},
                schema_hash="sha256:"
                + hashlib.sha256(pq.ParquetFile(root / n).schema_arrow.serialize()).hexdigest(),
                knowledge_cutoff=cutoff,
                parent_manifest_ids=(old.manifest_id,),
            )
        else:
            dataset = replace(
                old,
                partitions=tuple("parent/" + p for p in old.partitions),
                content_checksums={"parent/" + k: v for k, v in old.content_checksums.items()},
                parent_manifest_ids=(old.manifest_id,),
            )
        datasets.append(dataset)
    historical = replace(
        base.historical_bundle,
        root=root,
        datasets=tuple(datasets),
        canonical_identity_digest="sha256:" + expected,
        snapshot=SnapshotRef(
            base.historical_bundle.snapshot.market_date, cutoff, VERSION + ":" + expected, root.name
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
