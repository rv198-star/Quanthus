"""Source-supported reference correction preserves frozen science and P2 policy."""

import copy
import json
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_suspended_action_successor as s

R = Path(__file__).resolve().parents[2]


def test_exact_successor_preserves_all_science_and_matching():
    old = s.predecessor.load_request(R)
    new = s.load_request(R)
    assert all(new[k] == old[k] for k in s.PRESERVED)
    assert new["affected_security_ids"] == ["002606"]
    assert new["reference_carry_scope"]["existing_action_date"] == "2017-05-12"
    assert new["reference_carry_scope"]["blocked_observation"] == "2017-05-15"
    assert new["reference_carry_scope"]["no_added_or_shifted_action"] is True
    assert s.SuspendedActionRuntime._sell_schedule is s.parent.DatedShareRuntime._sell_schedule
    assert s.SuspendedActionRuntime.run_variant is s.parent.DatedShareRuntime.run_variant
    assert new["source_guards_waived"] is new["historical_PIT_claim"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", ["2017-01-01", "2021-12-30", "2021-12-31"]),
        ("affected_security_ids", ["000748"]),
        ("maximum_additional_wall_seconds", 7201),
        ("source_guards_waived", True),
        ("production_authorized", True),
        ("historical_PIT_claim", True),
        ("approved_proposal_sha256", "sha256:" + "a" * 64),
    ],
)
def test_scope_policy_or_authority_mutation_rejected(tmp_path, monkeypatch, field, value):
    old = s.predecessor.load_request(R)
    request = copy.deepcopy(s.load_request(R))
    request[field] = value
    (tmp_path / s.REQUEST).parent.mkdir(parents=True)
    (tmp_path / s.REQUEST).write_text(json.dumps(request))
    monkeypatch.setattr(s.predecessor, "load_request", lambda _: old)
    monkeypatch.setattr(s.native, "_sha", lambda _: request["parent_request_sha256"])
    with pytest.raises(ValueError, match="SUSPENDED_REQUEST"):
        s.load_request(tmp_path)


def test_missing_admission_cannot_open_source(tmp_path, monkeypatch):
    request = s.load_request(R)
    monkeypatch.setattr(s, "load_request", lambda _: request)
    monkeypatch.setattr(s.p10, "load_input", lambda *a: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="EXACT_ADMISSION"):
        s.SuspendedActionRuntime(tmp_path, tmp_path, tmp_path, {})
