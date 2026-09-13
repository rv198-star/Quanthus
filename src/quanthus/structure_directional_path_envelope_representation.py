"""Catalog-facing representation adapter for the E7 B6-PATH envelope proposal.

This module does not register a semantic concept and does not change the E5 algorithm. It only wraps
one Structure-owned T-ending envelope trace as a proposed catalog-compatible measurement payload.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.structure_directional_path_envelope import (
    PROPOSED_CONCEPT_ID,
    DirectionalPathEnvelopeTrace,
)
from quanthus.structure_v2_inputs import digest

CONTRACT_PATH = "config/structure-directional-path-envelope-catalog-representation.v1.json"
CONTRACT_DIGEST = "sha256:439653ff774e56dd05c79957b003b01e14022f8466cb8bd92140026784ad1beb"
RESULT_KIND = "measurement"
CONCEPT_VERSION = "0.2.0-synthetic"


class DirectionalPathEnvelopeRepresentationError(ValueError):
    """The proposed catalog-facing envelope representation is invalid."""


def load_contract(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        value = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalPathEnvelopeRepresentationError(
            "representation contract unreadable"
        ) from exc
    if digest(value) != CONTRACT_DIGEST:
        raise DirectionalPathEnvelopeRepresentationError("representation contract drift")
    return value


def measurement_value(trace: DirectionalPathEnvelopeTrace) -> dict[str, Any]:
    """Return the exact T-ending structured measurement value without changing E5 semantics."""
    source = trace.to_dict()
    return {
        "status": source["status"],
        "reason": source["reason"],
        "profile": source["profile"],
        "events": source["events"],
        "descriptors": source["descriptors"],
    }


def as_catalog_measurement(trace: DirectionalPathEnvelopeTrace) -> dict[str, Any]:
    """Wrap an E5 trace as a proposed catalog-compatible measurement observation value."""
    value = measurement_value(trace)
    return {
        "schema_version": "quanthus.directional-path-envelope-measurement-proposal.v1",
        "concept_id": PROPOSED_CONCEPT_ID,
        "concept_version": CONCEPT_VERSION,
        "result_kind": RESULT_KIND,
        "registration_status": "PROPOSED_NOT_REGISTERED",
        "value": value,
        "value_digest": digest(value),
        "claims": {
            "stage_projection": None,
            "compact_disorder_truth": None,
            "relocation_truth": None,
            "numeric_threshold_selected": False,
            "registered_fact": False,
            "events_are_independent_registered_facts": False,
            "descriptors_are_independent_registered_facts": False,
        },
    }
