"""Additive audit contracts for fact generation, publication and actual consumption.

These are receipts and operation/decision log events, NOT market Fact fields. No persistence,
Worker, API or legacy P2 DecisionEvent is changed. B2 must bind these contracts to verified
receipts and the existing append-only decision evidence store, rather than create a new log stack.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from quanthus.temporal_contracts import (
    ObservationRevision,
    TemporalQuery,
    contract_digest,
    require_instant,
    require_text,
)


@dataclass(frozen=True)
class GenerationReceipt:
    receipt_id: str
    run_id: str
    code_revision: str
    spec_digest: str
    output_revision_ids: tuple[str, ...]
    completed_at: datetime

    def __post_init__(self) -> None:
        for name in ("receipt_id", "run_id", "code_revision", "spec_digest"):
            require_text(getattr(self, name), name)
        require_instant(self.completed_at, "completed_at")
        if len(self.code_revision) != 40 or any(
            char not in "0123456789abcdef" for char in self.code_revision
        ):
            raise ValueError("generation must bind an exact code revision")
        if type(self.output_revision_ids) is not tuple:
            raise ValueError("output revisions must be an immutable tuple")
        if not self.output_revision_ids or len(set(self.output_revision_ids)) != len(
            self.output_revision_ids
        ):
            raise ValueError("generation requires unique output revisions")
        for revision in self.output_revision_ids:
            require_text(revision, "output_revision_id")


@dataclass(frozen=True)
class PublicationReceipt:
    receipt_id: str
    generation_ref: str
    artifact_digest: str
    servable_at: datetime
    delivery_mode: str = "atomic_package"

    def __post_init__(self) -> None:
        for name in ("receipt_id", "generation_ref", "artifact_digest"):
            require_text(getattr(self, name), name)
        require_instant(self.servable_at, "servable_at")
        if self.delivery_mode not in {"atomic_package", "direct_handoff"}:
            raise ValueError("delivery mode requires package or direct-handoff evidence")


@dataclass(frozen=True)
class ConsumedRevision:
    observation_key: str
    revision_id: str
    spec_digest: str
    generation_ref: str
    publication_ref: str
    artifact_digest: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            require_text(getattr(self, name), name)


@dataclass(frozen=True)
class FactConsumptionEvent:
    """One actual consumption operation; many events may reference the same immutable Fact.

    consumed_at/decision_at/recorded_at are actual operation clocks. A simulated/historical
    knowledge_cutoff belongs to query and is not backfilled into these clocks. For a reconstructed
    historical audit, old event times still require original evidence; recorded_at may be later.
    """

    event_id: str
    run_id: str
    consumer_id: str
    decision_id: str
    query: TemporalQuery
    consumed_at: datetime
    decision_at: datetime
    recorded_at: datetime
    inputs: tuple[ConsumedRevision, ...]

    def __post_init__(self) -> None:
        for name in ("event_id", "run_id", "consumer_id", "decision_id"):
            require_text(getattr(self, name), name)
        if not isinstance(self.query, TemporalQuery):
            raise ValueError("consumption requires typed query context")
        for name in ("consumed_at", "decision_at", "recorded_at"):
            require_instant(getattr(self, name), name)
        if not self.consumed_at <= self.decision_at <= self.recorded_at:
            raise ValueError("consumption/decision/log chronology is invalid")
        if self.query.knowledge_cutoff > self.decision_at:
            raise ValueError("decision cannot use a future knowledge cutoff")
        if type(self.inputs) is not tuple or not self.inputs or any(
            not isinstance(item, ConsumedRevision) for item in self.inputs
        ):
            raise ValueError("consumption requires exact typed input references")
        if len({item.revision_id for item in self.inputs}) != len(self.inputs):
            raise ValueError("duplicate consumed revision")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "quanthus.fact-consumption-event.v1", **asdict(self)}

    @property
    def digest(self) -> str:
        return contract_digest(self.to_dict())


def validate_consumption_binding(
    event: FactConsumptionEvent,
    observation: ObservationRevision,
    generation: GenerationReceipt,
    publication: PublicationReceipt,
) -> None:
    """Verify ONE local log-to-output binding; no source-PIT or execution authority granted.

    Historical recomputation may create its generation receipt years after result_as_of. It can
    only bind a correspondingly recent consumption event, not prove a historical expert used it.
    Receipt signatures, source closure, finality, partition and policy remain separate B2 gates.
    """
    matches = [item for item in event.inputs if item.revision_id == observation.revision_id]
    if len(matches) != 1:
        raise ValueError("consumption does not identify this exact observation revision")
    item = matches[0]
    if (
        item.observation_key != observation.observation_key
        or item.spec_digest != observation.spec_digest
        or generation.spec_digest != observation.spec_digest
        or observation.revision_id not in generation.output_revision_ids
        or item.generation_ref != generation.receipt_id
        or item.publication_ref != publication.receipt_id
        or publication.generation_ref != generation.receipt_id
        or item.artifact_digest != publication.artifact_digest
    ):
        raise ValueError("consumption provenance binding differs")
    if not observation.business_time.confirmed_by(event.query.knowledge_cutoff):
        raise ValueError("observation was not confirmed at the query cutoff")
    if generation.completed_at < max(
        observation.business_time.result_as_of,
        observation.business_time.confirmation_event_at or observation.business_time.result_as_of,
    ):
        raise ValueError("generation predates required market observations")
    if publication.servable_at < generation.completed_at:
        raise ValueError("publication predates generation")
    if publication.servable_at >= event.consumed_at:
        raise ValueError("delivery-before-consumption order is unproven")
