"""Opt-in P6 consumption of P10's Base 2.4 + explicit Daily source handoff.

This successor interface is distinct from the frozen legacy runtime. It has no
controller, activation or strategy-execution edge. P10 owns source scoping and
qualification; P6 independently validates that handoff before consuming it.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Any

from quanthus.backtest.base24_semantic_projection import KEYS
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.serialization import digest_value
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _fingerprint,
    _tree,
    physical_path,
)
from quanthus.pipelines.securities_warehouse.base24_p6_handoff import verify
from quanthus.pipelines.securities_warehouse.base24_p6_purpose import verify as verify_purposes

VERSION = "quanthus.p6-base24-consumer-binding.v1"


@dataclass(frozen=True)
class P6Base24Inputs:
    root: Path
    manifest_sha256: str
    identity: str
    manifest_json: str
    assessment_json: str
    fingerprints: Mapping[str, tuple[Any, ...]]

    @property
    def execution_authorized(self) -> bool:
        return False

    def assessment(self) -> dict[str, Any]:
        return dict(json.loads(self.assessment_json))

    def require_purpose(self, purpose: str) -> None:
        if self.assessment()["purposes"].get(purpose) is not True:
            fail("p6_base24_purpose_not_qualified", purpose)

    def iter_batches(
        self,
        dataset: str,
        *,
        purpose: str = "source_diagnostics",
        batch_size: int = 8192,
    ) -> Iterator[Any]:
        """Read only the physically isolated seven-table source view.

        A source diagnostic is not an official cycle or an observation. The
        default does not quietly turn a negative purpose finding into a grant.
        """
        if dataset not in KEYS or type(batch_size) is not int or not 1 <= batch_size <= 65536:
            fail("p6_base24_reader_scope", dataset)
        self.require_purpose(purpose)
        relative = "tables/" + dataset + ".parquet"
        path = physical_path(self.root / relative)
        if _fingerprint(path) != self.fingerprints[relative]:
            fail("p6_base24_member_changed", relative)
        pq = importlib.import_module("pyarrow.parquet")
        yield from pq.ParquetFile(path).iter_batches(batch_size=batch_size, use_threads=False)
        if _fingerprint(path) != self.fingerprints[relative]:
            fail("p6_base24_member_changed", relative)

    def historical_bundle(self) -> HistoricalReplayBundle:
        """Existing feature/frame-reader interface, only after source qualification.

        This method does not supply runtime/deployment/activation authority. A
        governance snapshot whose strict historical purpose fails is rejected
        before returning any research/frame-construction capability.
        """
        self.require_purpose("historical_replay")
        m = json.loads(self.manifest_json)
        cutoff = datetime.fromisoformat(m["knowledge_cutoff"])
        datasets = []
        for name in KEYS:
            relative = "tables/" + name + ".parquet"
            member = m["files"][relative]
            datasets.append(
                DatasetManifest(
                    dataset=name,
                    version=VERSION,
                    source_revisions={"p10_handoff": self.manifest_sha256},
                    row_count=member["rows"],
                    partitions=(relative,),
                    schema_hash=digest_value(member["schema"]),
                    content_checksums={relative: member["sha256"]},
                    created_at=datetime.fromisoformat(m["built_at"]),
                    knowledge_cutoff=cutoff,
                )
            )
        return HistoricalReplayBundle(
            root=self.root,
            snapshot=SnapshotRef(
                date.fromisoformat(m["business_end"]),
                cutoff,
                self.identity,
                "p6-base24-input-" + self.identity[7:31],
            ),
            universe_policy_version=VERSION,
            datasets=tuple(datasets),
            canonical_identity_digest=self.identity,
        )


def open_base24_inputs(
    root: Path, *, manifest_sha256: str, scratch_root: Path | None = None
) -> P6Base24Inputs:
    """Bind the exact standalone P10 handoff; never dereference old source roots."""
    root = physical_path(root)
    assessment = verify(root, manifest_sha256, scratch_root=scratch_root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    identity = digest_value(
        {
            "schema_version": VERSION,
            "handoff_manifest_sha256": manifest_sha256,
            "business_start": manifest["business_start"],
            "business_end": manifest["business_end"],
            "knowledge_cutoff": manifest["knowledge_cutoff"],
            "venues": manifest["venues"],
            "legacy_runtime_equivalence": False,
            "execution_authorized": False,
        }
    )
    return P6Base24Inputs(
        root,
        manifest_sha256,
        identity,
        json.dumps(manifest, sort_keys=True),
        json.dumps(assessment, sort_keys=True),
        MappingProxyType(
            {relative: _fingerprint(root / relative) for relative in manifest["files"]}
        ),
    )


@dataclass(frozen=True)
class P6Base24QualifiedContext:
    """Later-snapshot source context; no historical replay or activation edge."""

    root: Path
    identity: str
    knowledge_cutoff: datetime
    assessment_json: str
    fingerprints: Mapping[str, tuple[Any, ...]]
    qualification_partitions: tuple[str, ...]

    @property
    def execution_authorized(self) -> bool:
        return False

    def iter_batches(self, dataset: str, *, batch_size: int = 8192) -> Iterator[Any]:
        if (
            dataset not in {"daily_ohlcv_none", "execution_qualification", "action_qualification"}
            or type(batch_size) is not int
            or not 1 <= batch_size <= 65536
        ):
            fail("p6_base24_reader_scope", dataset)

        def unchanged() -> None:
            for rel, fingerprint in self.fingerprints.items():
                if _fingerprint(physical_path(self.root / rel)) != fingerprint:
                    fail("p6_base24_member_changed", rel)

        unchanged()
        from quanthus.pipelines.securities_warehouse.base24_p6_handoff import _connection, _q

        with TemporaryDirectory(prefix="p6-later-context-") as directory:
            c = _connection(Path(directory))
            try:
                status = (
                    "read_parquet(["
                    + ",".join(_q(self.root / rel) for rel in self.qualification_partitions)
                    + "])"
                )
                if dataset == "daily_ohlcv_none":
                    bars = _q(self.root / "source/tables/daily_ohlcv_none.parquet")
                    query = (
                        f"SELECT b.* FROM read_parquet({bars}) b "
                        f"JOIN {status} q ON q.listing_id=b.dwd_listing_id "
                        "AND q.market_date=b.date "
                        "WHERE q.warmup_eligible ORDER BY b.date,b.dwd_listing_id"
                    )
                elif dataset == "execution_qualification":
                    query = "SELECT * FROM " + status + " ORDER BY market_date,listing_id"
                else:
                    query = (
                        "SELECT * FROM read_parquet("
                        + _q(self.root / "qualified/actions.parquet")
                        + ") ORDER BY action_id"
                    )
                yield from c.execute(query).to_arrow_reader(batch_size=batch_size)
            finally:
                c.close()
        unchanged()


def open_base24_qualified_context(
    root: Path, *, manifest_sha256: str, decision_knowledge_cutoff: datetime
) -> P6Base24QualifiedContext:
    """Consume exact qualified subsets without changing the strict v1 reader.

    Context data is usable only at/after its real source snapshot cutoff. A
    future official cycle still needs its own Daily, visibility receipt and
    accepted activation/runtime binding. This API cannot mint those authorities.
    """
    root = physical_path(root)
    if decision_knowledge_cutoff.tzinfo is None or decision_knowledge_cutoff.utcoffset() is None:
        fail("p6_base24_context_cutoff", "timezone required")
    relatives_before = _tree(root)
    before = {rel: _fingerprint(root / rel) for rel in relatives_before}
    assessment = verify_purposes(root, manifest_sha256)
    source_manifest = json.loads((root / "source/manifest.json").read_bytes())
    source_cutoff = datetime.fromisoformat(source_manifest["knowledge_cutoff"])
    if decision_knowledge_cutoff < source_cutoff:
        fail("p6_base24_context_cutoff", "source snapshot not yet known")
    manifest = json.loads((root / "manifest.json").read_bytes())
    relatives = [*manifest["files"], "manifest.json", "READY"]
    if _tree(root) != relatives_before or any(
        _fingerprint(root / rel) != fingerprint for rel, fingerprint in before.items()
    ):
        fail("p6_base24_member_changed", "during qualified context binding")
    return P6Base24QualifiedContext(
        root,
        digest_value(
            {
                "manifest_sha256": manifest_sha256,
                "purpose": "later_snapshot_source_context",
                "decision_knowledge_cutoff": decision_knowledge_cutoff.isoformat(),
                "execution_authorized": False,
            }
        ),
        decision_knowledge_cutoff,
        json.dumps(assessment, sort_keys=True),
        MappingProxyType({rel: before[rel] for rel in relatives}),
        tuple(sorted(rel for rel in manifest["files"] if rel.endswith("/execution.parquet"))),
    )
