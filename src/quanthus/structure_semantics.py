"""Time-first Structure naming contracts; not a fact builder or execution authority.

This additive module does not change v1 FeatureFact IDs, the DWD provider, or any Worker.
Clock checks test a supplied evidence envelope only. The caller must separately verify the
exact source revision, evidence closure, quality, partition, and consumer authorization.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

_SEGMENT = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_BOUNDARIES = {"owned", "parallel", "required_reference", "evaluation_only"}
_KINDS = {"reference", "measurement", "event", "relation", "process"}


def _parts(value: str, *, lengths: tuple[int, ...]) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("semantic identifier must be text")
    parts = tuple(value.split("."))
    if len(parts) not in lengths or any(_SEGMENT.fullmatch(part) is None for part in parts):
        raise ValueError("semantic identifier requires complete lowercase kebab-case segments")
    return parts


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("semantic catalog fields differ")
    return value


@dataclass(frozen=True)
class SemanticFamily:
    prefix: str
    meaning: str
    boundary: str


@dataclass(frozen=True)
class SemanticConcept:
    concept_id: str
    meaning: str
    result_kind: str
    registration: str


@dataclass(frozen=True)
class SemanticCatalog:
    families: tuple[SemanticFamily, ...]
    concepts: tuple[SemanticConcept, ...]

    def family_of(self, concept_id: str) -> SemanticFamily:
        """Classify syntax by an exact two-segment prefix; this does not register a concept."""
        parts = _parts(concept_id, lengths=(3,))
        prefix = ".".join(parts[:2])
        for family in self.families:
            if family.prefix == prefix:
                return family
        raise ValueError(f"unregistered semantic family: {prefix}")

    def resolve(self, concept_id: str) -> SemanticConcept:
        self.family_of(concept_id)
        for concept in self.concepts:
            if concept.concept_id == concept_id:
                return concept
        raise ValueError(f"unregistered semantic concept: {concept_id}")

    def discover(self, prefix: str) -> tuple[SemanticConcept, ...]:
        """Return registered concepts using segment-boundary matching, never substring search."""
        parts = _parts(prefix, lengths=(1, 2, 3))
        return tuple(
            item
            for item in sorted(self.concepts, key=lambda item: item.concept_id)
            if tuple(item.concept_id.split("."))[: len(parts)] == parts
        )


def load_semantic_catalog(path: Path) -> SemanticCatalog:
    raw = _exact(
        json.loads(path.read_text(encoding="utf-8")),
        {
            "schema_version", "catalog_id", "authority", "governing_design",
            "families", "concepts", "legacy_links",
        },
    )
    if (
        raw["schema_version"] != "1.0.0"
        or raw["catalog_id"] != "structure-semantic-catalog"
        or raw["authority"] != "naming_and_boundary_contract_only"
        or not isinstance(raw["governing_design"], str)
        or not raw["governing_design"]
    ):
        raise ValueError("semantic catalog identity or authority differs")
    if any(not isinstance(raw[key], list) for key in ("families", "concepts", "legacy_links")):
        raise ValueError("semantic catalog collections must be arrays")
    families = []
    for value in raw["families"]:
        item = _exact(value, {"prefix", "meaning", "boundary"})
        _parts(item["prefix"], lengths=(2,))
        if (
            item["boundary"] not in _BOUNDARIES
            or not isinstance(item["meaning"], str)
            or not item["meaning"].strip()
        ):
            raise ValueError("semantic family boundary or meaning is invalid")
        families.append(SemanticFamily(**item))
    concepts = []
    for value in raw["concepts"]:
        item = _exact(value, {"concept_id", "meaning", "result_kind", "registration"})
        _parts(item["concept_id"], lengths=(3,))
        if (
            item["result_kind"] not in _KINDS
            or item["registration"] != "reserved"
            or not isinstance(item["meaning"], str)
            or not item["meaning"].strip()
        ):
            raise ValueError("semantic concept is invalid or attempts runtime promotion")
        concepts.append(SemanticConcept(**item))
    if not families or not concepts:
        raise ValueError("semantic catalog must not be empty")
    if len({item.prefix for item in families}) != len(families):
        raise ValueError("duplicate semantic family")
    if len({item.concept_id for item in concepts}) != len(concepts):
        raise ValueError("duplicate semantic concept")
    catalog = SemanticCatalog(tuple(families), tuple(concepts))
    for concept in catalog.concepts:
        catalog.family_of(concept.concept_id)
    legacy_keys = set()
    for value in raw["legacy_links"]:
        item = _exact(
            value, {"legacy_id", "legacy_version", "related_concept", "relationship", "note"}
        )
        if any(not isinstance(field, str) or not field.strip() for field in item.values()):
            raise ValueError("legacy relation fields must be nonempty text")
        key = (item["legacy_id"], item["legacy_version"])
        if key in legacy_keys or item["relationship"] != "related_not_equivalent":
            raise ValueError("duplicate or silently equivalent legacy relation")
        legacy_keys.add(key)
        catalog.resolve(item["related_concept"])
    return catalog


class ClockView(str, Enum):
    PLATFORM_AS_KNOWN = "platform_as_known"
    PUBLIC_RECONSTRUCTION = "public_reconstruction"
    RETROSPECTIVE = "retrospective"


def _aware(value: datetime | None, name: str) -> None:
    if value is not None and (
        not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be a timezone-aware datetime")


@dataclass(frozen=True)
class TemporalEvidence:
    """Clock envelope for one exact source-version closure of a structural observation.

    Public and platform times have separate evidence references. Null is unknown, never the
    market date. This object checks local consistency, not authenticity of those references.
    event_end is an observation endpoint, not a financial-report forecast period.

    Retained B1 helper: confirmed_at denotes the market confirmation endpoint, NOT the later
    source/confirmation readiness time. computed_at is materialization metadata, not actual-use
    evidence. New B1-R callers use temporal_contracts and separate evidence_consumption logs.
    """

    source_version_ref: str
    event_end: datetime
    confirmed_at: datetime
    computed_at: datetime
    public_available_at: datetime | None = None
    public_evidence_ref: str | None = None
    system_known_from: datetime | None = None
    system_known_to: datetime | None = None
    system_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_version_ref, str) or not self.source_version_ref.strip():
            raise ValueError("exact source-version reference is required")
        for name in ("event_end", "confirmed_at", "computed_at"):
            if getattr(self, name) is None:
                raise ValueError(f"{name} is required")
        for name in (
            "event_end", "confirmed_at", "computed_at", "public_available_at",
            "system_known_from", "system_known_to",
        ):
            _aware(getattr(self, name), name)
        if self.confirmed_at < self.event_end or self.computed_at < self.confirmed_at:
            raise ValueError("structural confirmation/computation chronology is invalid")
        for clock, evidence in (
            (self.public_available_at, self.public_evidence_ref),
            (self.system_known_from, self.system_evidence_ref),
        ):
            invalid_reference = evidence is not None and (
                not isinstance(evidence, str) or not evidence.strip()
            )
            if (clock is None) != (evidence is None) or invalid_reference:
                raise ValueError("availability clock requires its own evidence reference")
        if self.system_known_to is not None and (
            self.system_known_from is None or self.system_known_to <= self.system_known_from
        ):
            raise ValueError("knowledge interval must be nonempty and half-open")


@dataclass(frozen=True)
class ClockCheck:
    clock_eligible: bool
    view: ClockView
    reason: str


def check_clock_at(evidence: TemporalEvidence, cutoff: datetime, view: ClockView) -> ClockCheck:
    """Legacy local source-clock check, not output-publication or actual-consumption proof.

    Preserve B1 behavior for existing callers/tests. Version selection, readiness and actual-use
    bindings use the additive B1-R contracts; neither API grants data or order authority.
    """
    if cutoff is None:
        raise ValueError("cutoff is required")
    _aware(cutoff, "cutoff")
    view = ClockView(view)
    if evidence.confirmed_at > cutoff:
        return ClockCheck(False, view, "not_confirmed_at_cutoff")
    if view is ClockView.PLATFORM_AS_KNOWN:
        if evidence.system_known_from is None:
            return ClockCheck(False, view, "platform_knowledge_unproven")
        eligible = evidence.system_known_from <= cutoff and (
            evidence.system_known_to is None or cutoff < evidence.system_known_to
        )
        return ClockCheck(
            eligible, view,
            "platform_clock_matches" if eligible else "outside_knowledge_interval",
        )
    if view is ClockView.PUBLIC_RECONSTRUCTION:
        if evidence.public_available_at is None:
            return ClockCheck(False, view, "public_availability_unproven")
        eligible = evidence.public_available_at <= cutoff
        return ClockCheck(
            eligible, view,
            "public_reconstruction_only" if eligible else "not_public_at_cutoff",
        )
    return ClockCheck(True, view, "retrospective_only_not_historical_availability_proof")
