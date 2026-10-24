"""New sealed retrospective input, with actual credit explicitly unavailable."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from quanthus.contracts import SnapshotRef

from . import p10_historical_action_input as parent
from . import p10_historical_distribution_source as official
from . import p10_rights_replay_input as physical

VERSION = "p10-retrospective-dated-distribution-input-v1"
PARENT_SHA = "777b6db164ba4070a8ad63c2074c9efe0b397ec134ecb994ba2055ea8cb371cb"


def _facts(root: Path, identity: str) -> Any:
    facts = official.load_source(root, identity)
    if any(
        f.cash_payment_date != f.ex_date
        or not f.bonus_share_ratio
        or f.share_listing_date is None
        or f.share_listing_date <= f.ex_date
        or f.share_credit_date is not None
        for f in facts
    ):
        raise ValueError("DATED_INPUT_SOURCE_DATES")
    return facts


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
        raise ValueError("DATED_INPUT_ISOLATION")
    base, old = parent.load_input(source, PARENT_SHA)
    facts = _facts(official_root, official_sha)
    if {(f.security_code, f.ex_date) for f in old}.intersection(
        (f.security_code, f.ex_date) for f in facts
    ):
        raise ValueError("DATED_INPUT_DUPLICATE_ACTION")
    target.mkdir()
    shutil.copytree(source, target / "parent", copy_function=shutil.copy2)
    shutil.copytree(official_root, target / "official", copy_function=shutil.copy2)
    (target / "effective").mkdir()
    stats = parent.rewrite_limits(
        base.historical_bundle.partition_paths("price_limit_execution_daily")[0],
        target / "effective/price_limits.parquet",
        facts,
    )
    members = [
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
        "source_information_cutoff": max(f.acquired_at for f in (*old, *facts)).isoformat(),
        "dated_keys": [[f.security_code, f.ex_date.isoformat()] for f in facts],
        "scope": ["2012-01-04", "2021-12-30", "2021-12-31"],
        "historical_PIT_claim": False,
        "production_authorized": False,
        "actual_share_credit_known": False,
        "reference_model": parent.MODEL,
        "producer_code_sha256": official.sha(Path(__file__)),
        "stats": stats,
        "files": members,
    }
    raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (target / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (target / "SOURCE_READY").write_text(digest + "\n")
    return {"manifest_sha256": digest, "stats": stats, "bytes": sum(r["bytes"] for r in members)}


def load_input(root: Path, expected: str) -> Any:
    import pyarrow.parquet as pq

    paths = physical._files(root)
    if (
        official.sha(root / "manifest.json") != expected
        or (root / "SOURCE_READY").read_text() != expected + "\n"
    ):
        raise ValueError("DATED_INPUT_IDENTITY")
    m = json.loads((root / "manifest.json").read_bytes())
    if (
        m.get("kind") != VERSION
        or m.get("parent_manifest_sha256") != PARENT_SHA
        or m.get("reference_model") != parent.MODEL
        or m.get("scope") != ["2012-01-04", "2021-12-30", "2021-12-31"]
        or any(
            m.get(k) is not False
            for k in ("historical_PIT_claim", "production_authorized", "actual_share_credit_known")
        )
    ):
        raise ValueError("DATED_INPUT_SCOPE")
    declared = {"manifest.json", "SOURCE_READY"}
    for row in m["files"]:
        name = row["path"]
        if name in declared or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("DATED_INPUT_MEMBER")
        declared.add(name)
        p = root / name
        if p.stat().st_size != row["bytes"] or official.sha(p) != row["sha256"]:
            raise ValueError("DATED_INPUT_MEMBER_CHANGED")
    if {p.relative_to(root).as_posix() for p in paths} != declared:
        raise ValueError("DATED_INPUT_PHYSICAL_SET")
    base, old = parent.load_input(root / "parent", PARENT_SHA)
    facts = _facts(root / "official", m["official_manifest_sha256"])
    cutoff = max(f.acquired_at for f in (*old, *facts))
    if (
        datetime.fromisoformat(m["source_information_cutoff"]) != cutoff
        or m["dated_keys"] != [[f.security_code, f.ex_date.isoformat()] for f in facts]
        or len({(f.security_code, f.ex_date) for f in (*old, *facts)}) != len(old) + len(facts)
        or parent.rewrite_limits(
            base.historical_bundle.partition_paths("price_limit_execution_daily")[0],
            root / "effective/price_limits.parquet",
            facts,
            verify_only=True,
        )
        != m["stats"]
    ):
        raise ValueError("DATED_INPUT_RECOMPUTED_CLOSURE")
    datasets = []
    for original in base.historical_bundle.datasets:
        if original.dataset == "price_limit_execution_daily":
            name = "effective/price_limits.parquet"
            dataset = replace(
                original,
                version=VERSION,
                partitions=(name,),
                content_checksums={name: "sha256:" + official.sha(root / name)},
                schema_hash="sha256:"
                + hashlib.sha256(pq.ParquetFile(root / name).schema_arrow.serialize()).hexdigest(),
                knowledge_cutoff=cutoff,
                parent_manifest_ids=(original.manifest_id,),
            )
        else:
            dataset = replace(
                original,
                partitions=tuple("parent/" + p for p in original.partitions),
                content_checksums={"parent/" + k: v for k, v in original.content_checksums.items()},
                parent_manifest_ids=(original.manifest_id,),
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
    ), (*old, *facts)
