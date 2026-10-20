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
from types import MappingProxyType
from typing import Any

from quanthus.backtest.base24_semantic_projection import KEYS
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.serialization import digest_value
from quanthus.pipelines.securities_warehouse.base22_sources import _fingerprint, physical_path
from quanthus.pipelines.securities_warehouse.base24_p6_handoff import verify

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


def open_base24_inputs(root: Path, *, manifest_sha256: str) -> P6Base24Inputs:
    """Bind the exact standalone P10 handoff; never dereference old source roots."""
    root = physical_path(root)
    assessment = verify(root, manifest_sha256)
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
