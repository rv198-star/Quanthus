"""Frozen science, exact source authority and unchanged execution semantics."""

import copy
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_tch_held_action_preparation as prep
from quanthus.expert_strategy import p3_tch_held_action_successor as s
from quanthus.expert_strategy.p3_dated_share_preparation import replace_raw_population

R = Path(__file__).resolve().parents[2]


def test_request_preserves_all_science_and_dated_share_policy():
    parent = s.parent.load_request(R)
    request = s.load_request(R)
    assert all(request[k] == parent[k] for k in s.PRESERVED)
    assert request["affected_security_ids"] == ["000748"]
    assert request["maximum_additional_wall_seconds"] == 7200
    assert request["production_authorized"] is False
    assert prep.replace_raw_population is replace_raw_population
    assert s.TCHHeldActionRuntime._sell_schedule is s.parent.DatedShareRuntime._sell_schedule
    assert s.TCHHeldActionRuntime.run_variant is s.parent.DatedShareRuntime.run_variant


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", ["2015-01-01", "2021-12-30", "2021-12-31"]),
        ("affected_security_ids", ["000749"]),
        ("maximum_additional_wall_seconds", 7201),
        ("source_guards_waived", True),
        ("production_authorized", True),
        ("approved_proposal_sha256", "sha256:" + "a" * 64),
    ],
)
def test_request_mutation_rejected(tmp_path, monkeypatch, field, value):
    import json

    request = copy.deepcopy(s.load_request(R))
    request[field] = value
    (tmp_path / s.REQUEST).parent.mkdir(parents=True)
    (tmp_path / s.REQUEST).write_text(json.dumps(request))
    monkeypatch.setattr(
        s.parent, "load_request", lambda _: json.loads((R / s.parent.REQUEST).read_bytes())
    )
    monkeypatch.setattr(s.native, "_sha", lambda p: request["parent_request_sha256"])
    with pytest.raises(ValueError, match="TCH_HELD_REQUEST"):
        s.load_request(tmp_path)


def test_missing_admission_blocks_real_input_before_open(tmp_path, monkeypatch):
    request = s.load_request(R)
    monkeypatch.setattr(s, "load_request", lambda _: request)
    monkeypatch.setattr(s.p10, "load_input", lambda *a: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="EXACT_ADMISSION"):
        s.TCHHeldActionRuntime(tmp_path, tmp_path, tmp_path, {})


@pytest.mark.parametrize("case", ["same", "later-envelope", "mixed", "empty", "naive"])
def test_raw_before_image_uses_its_own_frozen_clock_not_latest_envelope(case):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    old = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
    latest = datetime(2026, 10, 25, 5, tzinfo=timezone.utc)
    rows = [SimpleNamespace(security_id="affected", knowledge_cutoff=old)]
    expected = old.isoformat()
    if case == "later-envelope":
        expected = latest.isoformat()
    if case == "mixed":
        rows.append(SimpleNamespace(security_id="affected", knowledge_cutoff=latest))
    if case == "empty":
        rows = []
    if case == "naive":
        expected = "2026-10-24T16:00:00"
    raw = SimpleNamespace(raw_candidates=rows)
    if case == "same":
        assert prep.prior_raw_cutoff(raw, ("affected",), expected) == expected
    else:
        with pytest.raises(ValueError, match="RAW_CLOCK_BEFORE_IMAGE"):
            prep.prior_raw_cutoff(raw, ("affected",), expected)
