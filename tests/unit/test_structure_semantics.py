from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.structure_semantics import (
    ClockView,
    TemporalEvidence,
    check_clock_at,
    load_semantic_catalog,
)

ROOT = find_project_root()
CATALOG = ROOT / "config/structure-semantic-catalog.v1.json"
TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 15) -> datetime:
    return datetime(2020, 1, day, hour, tzinfo=TZ)


def evidence() -> TemporalEvidence:
    return TemporalEvidence(
        source_version_ref="source-v1-exact-closure",
        event_end=at(3),
        confirmed_at=at(3),
        computed_at=datetime(2026, 9, 6, tzinfo=TZ),
        public_available_at=at(3, 16),
        public_evidence_ref="publisher-receipt-v1",
        system_known_from=at(3, 17),
        system_known_to=at(10),
        system_evidence_ref="platform-receipt-v1",
    )


def test_catalog_separates_discovery_registration_and_authority() -> None:
    catalog = load_semantic_catalog(CATALOG)
    assert catalog.family_of("structure.anchor.rolling-high").prefix == "structure.anchor"
    assert catalog.family_of("indicator.trend.sma").boundary == "parallel"
    assert catalog.family_of("reference.corporate-action.split").boundary == "required_reference"
    assert catalog.family_of("outcome.structure.breach").boundary == "evaluation_only"
    assert catalog.resolve("structure.interaction.close-reclaim").result_kind == "event"
    assert catalog.resolve("structure.episode.breakout").registration == "reserved"
    assert catalog.discover("structure.anc") == ()
    assert catalog.discover("struct") == ()
    assert len(catalog.discover("structure.anchor")) == 3
    assert len(catalog.discover("structure.anchor.rolling-high")) == 1
    assert all(item.concept_id.startswith("structure.") for item in catalog.discover("structure"))
    with pytest.raises(ValueError, match="unregistered semantic concept"):
        catalog.resolve("structure.anchor.imagined")


@pytest.mark.parametrize(
    "name",
    ["structurex.anchor.high", "structure.anchors.high", "Structure.anchor.high",
     "structure.anchor.high@1.0.0", "structure.anchor.ma_10", "structure.anchor", "ma10"],
)
def test_names_cannot_silently_guess_family_or_formula(name: str) -> None:
    with pytest.raises(ValueError):
        load_semantic_catalog(CATALOG).family_of(name)


@pytest.mark.parametrize("mutation", ["duplicate", "extra", "promotion", "equivalence"])
def test_catalog_rejects_ambiguous_or_promoting_edits(tmp_path: Path, mutation: str) -> None:
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        raw["families"].append(raw["families"][0])
    elif mutation == "extra":
        raw["concepts"][0]["execution_authorized"] = True
    elif mutation == "promotion":
        raw["concepts"][0]["registration"] = "production"
    else:
        raw["legacy_links"][0]["relationship"] = "exact_alias"
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_semantic_catalog(target)


def test_old_feature_identity_is_not_a_new_concept_alias() -> None:
    with pytest.raises(ValueError):
        load_semantic_catalog(CATALOG).resolve("price.sma20")
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    sma = next(item for item in raw["legacy_links"] if item["legacy_id"] == "price.sma20")
    assert sma["relationship"] == "related_not_equivalent"
    assert "index" in sma["note"]


def test_public_reconstruction_is_not_what_platform_knew() -> None:
    row = evidence()
    public = check_clock_at(row, at(3, 16), ClockView.PUBLIC_RECONSTRUCTION)
    actual = check_clock_at(row, at(3, 16), ClockView.PLATFORM_AS_KNOWN)
    assert public.clock_eligible is True
    assert public.reason == "public_reconstruction_only"
    assert actual.clock_eligible is False
    assert actual.reason == "outside_knowledge_interval"


def test_later_computation_does_not_erase_earlier_knowledge_interval() -> None:
    row = evidence()
    assert check_clock_at(row, at(5), ClockView.PLATFORM_AS_KNOWN).clock_eligible
    assert not check_clock_at(row, at(10), ClockView.PLATFORM_AS_KNOWN).clock_eligible
    corrected = replace(
        row,
        source_version_ref="source-v2-corrected",
        system_known_from=at(10),
        system_known_to=None,
        public_available_at=at(10),
    )
    assert not check_clock_at(corrected, at(5), ClockView.PLATFORM_AS_KNOWN).clock_eligible
    assert not check_clock_at(corrected, at(5), ClockView.PUBLIC_RECONSTRUCTION).clock_eligible
    assert check_clock_at(corrected, at(10), ClockView.PLATFORM_AS_KNOWN).clock_eligible


def test_delayed_confirmation_never_backdates_observation() -> None:
    row = replace(evidence(), confirmed_at=at(7))
    for view in ClockView:
        assert not check_clock_at(row, at(6), view).clock_eligible
    assert check_clock_at(row, at(7), ClockView.PLATFORM_AS_KNOWN).clock_eligible


def test_unknown_source_clock_is_not_inferred_from_market_date() -> None:
    row = TemporalEvidence("retrospective-only-source", at(3), at(3), at(12))
    assert (
        check_clock_at(row, at(5), ClockView.PLATFORM_AS_KNOWN).reason
        == "platform_knowledge_unproven"
    )
    assert (
        check_clock_at(row, at(5), ClockView.PUBLIC_RECONSTRUCTION).reason
        == "public_availability_unproven"
    )
    diagnostic = check_clock_at(row, at(5), ClockView.RETROSPECTIVE)
    assert diagnostic.clock_eligible
    assert diagnostic.reason == "retrospective_only_not_historical_availability_proof"


def test_source_receipt_after_close_cannot_be_consumed_at_close() -> None:
    for view in (ClockView.PLATFORM_AS_KNOWN, ClockView.PUBLIC_RECONSTRUCTION):
        assert not check_clock_at(evidence(), at(3), view).clock_eligible


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed_at": at(2)},
        {"computed_at": at(2)},
        {"system_known_to": at(3)},
        {"public_evidence_ref": None},
        {"system_evidence_ref": ""},
        {"source_version_ref": ""},
        {"public_available_at": datetime(2020, 1, 3)},
    ],
)
def test_temporal_envelope_rejects_ambiguous_local_clocks(changes: dict) -> None:
    with pytest.raises(ValueError):
        replace(evidence(), **changes)


def test_timezone_equivalence_and_unknown_views() -> None:
    row = evidence()
    instant = at(5)
    assert check_clock_at(row, instant, ClockView.PLATFORM_AS_KNOWN) == check_clock_at(
        row, instant.astimezone(timezone.utc), ClockView.PLATFORM_AS_KNOWN
    )
    with pytest.raises(ValueError):
        check_clock_at(row, datetime(2020, 1, 5), ClockView.PLATFORM_AS_KNOWN)
    with pytest.raises(ValueError):
        check_clock_at(row, instant, "best_effort")  # type: ignore[arg-type]


def test_blueprint_preserves_gaps_and_has_no_dictionary_dependency_cycle() -> None:
    raw = json.loads((ROOT / "config/structure-facts-v2-blueprint.v1.json").read_text())
    assert raw["v2_runtime_accepted"] is False
    assert raw["dictionary_blocks_current_slice"] is False
    assert raw["current_slice"] == "B1-R"
    rows = {item["id"]: item for item in raw["capabilities"]}
    assert len(rows) == len(raw["capabilities"])
    assert rows["B1"]["implementation_status"] == "contract_only"
    assert rows["B1-R"]["implementation_status"] == "contract_only"
    assert rows["B1-R"]["depends_on"] == ["B1"]
    assert rows["B2"]["depends_on"] == ["B1-R"]
    assert rows["B2"]["implementation_status"] == "not_started"
    assert rows["B8"]["implementation_status"] == "separate_backlog"
    assert rows["B9"]["implementation_status"] == "outside_structure_scope"

    def visit(key: str, ancestors: frozenset[str]) -> None:
        assert key not in ancestors
        assert key in rows
        item = rows[key]
        assert item["missing"] and item["next_gate"]
        for path in item["evidence"]:
            assert (ROOT / path).is_file(), path
        for dependency in item["depends_on"]:
            visit(dependency, ancestors | {key})

    for key in rows:
        visit(key, frozenset())
    assert all("B8" not in rows[key]["depends_on"] for key in ("B1", "B2", "B3", "B4", "B5"))
