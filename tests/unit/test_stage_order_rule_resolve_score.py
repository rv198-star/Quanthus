from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning import order_reference_protocol as protocol
from quanthus.stage_learning import order_rule_resolve_score as api
from quanthus.structure_v2_inputs import digest

from .test_stage_order_reference_protocol import make_evidence

ROOT = find_project_root()


def test_config_and_frozen_review_ledger_are_exactly_bound() -> None:
    config = api.load_config()
    assert digest(config) == api.CONFIG_DIGEST
    raw, decisions = api._parse_decisions(ROOT, config)
    assert len(decisions) == 160
    assert len({item["case_id"] for item in decisions}) == 160
    assert api._sha(raw) == config["review_decisions"]["sha256"]
    assert config["review_decisions"]["expected_reference_preview"] == {
        "STAGE_0": 30,
        "STAGE_2": 46,
        "NEITHER": 36,
        "AMBIGUOUS": 48,
    }
    assert config["review_decisions"]["kind"] == "self_simulated"
    assert config["review_decisions"]["candidate_output_seen_during_case_review"] is False
    assert config["review_decisions"]["cognitive_blinding_claimed"] is False


def test_build_opinions_formats_frozen_semantics_without_inference() -> None:
    evidence = make_evidence()
    case = evidence["cases"][0]
    config = copy.deepcopy(api.load_config())
    config["prepare"]["case_count"] = 1
    config["review_decisions"]["case_count"] = 1
    config["review_decisions"]["opinion_count"] = 2
    decision = {
        "index": 0,
        "case_id": case["case_id"],
        "short_primary": "U2",
        "fund_primary": "S2",
        "recent_width": "EXPANDING",
        "recent_center": "STABLE",
        "short_reason": "Recent expansion keeps the short-term call unresolved.",
        "fund_reason": "Main-scale contraction remains orderly.",
    }
    opinions = api.build_opinions(evidence, [decision], config)
    assert len(opinions) == 2
    short, fund = opinions
    assert short["primary"] == {"stage_0": "FALSE", "stage_2": "UNKNOWN"}
    assert fund["primary"] == {"stage_0": "FALSE", "stage_2": "TRUE"}
    assert short["provenance"] == fund["provenance"] == {
        "kind": "self_simulated",
        "origin_id": "chatgpt-owner-session-stage007",
        "session_id": "stage007-reference-review-20260911",
    }
    assert short["reviewer_id"] != fund["reviewer_id"]
    assert short["unknowns"]
    assert fund["support_refs"]
    assert fund["contradiction_refs"]  # EXPANDING recent evidence is not mislabeled support.
    resolved, summary = protocol.resolve_opinions(evidence, opinions)
    assert resolved[0]["primary_reference"] == "AMBIGUOUS"
    assert summary["distinct_origin_count"] == 1
    assert summary["independent_validation"] is False


def test_review_ledger_tamper_is_fail_closed(tmp_path: Path) -> None:
    config = copy.deepcopy(api.load_config())
    source = ROOT / config["review_decisions"]["path"]
    changed = tmp_path / "decisions.tsv"
    changed.write_bytes(source.read_bytes().replace(b"|S0|", b"|S2|", 1))
    config["review_decisions"]["path"] = str(changed)
    with pytest.raises(api.OrderRuleResolveScoreError):
        api._parse_decisions(ROOT, config)


def test_resolve_score_seals_reference_before_candidate_score(tmp_path: Path) -> None:
    config = api.load_config()
    prepare = config["prepare"]
    output = tmp_path / "runs"
    output.mkdir()
    decisions_raw, decisions = api._parse_decisions(ROOT, config)
    evidence = {"cases": [{"case_id": item["case_id"]} for item in decisions]}
    selection = {"selection_digest": prepare["selection_digest"]}
    events: list[str] = []

    def object_side_effect(path: Path, _label: str):
        return evidence if path.name == "evidence.json" else selection

    def publish_side_effect(target, _evidence, _opinions):
        events.append("seal")
        target.mkdir()
        return {
            "manifest_sha256": "sha256:" + "a" * 64,
            "primary_reference_counts": config["review_decisions"]["expected_reference_preview"],
        }

    def score_side_effect(*_args, **_kwargs):
        assert events == ["seal"]
        events.append("score")
        return {
            "rule_digest": api.RULE_DIGEST,
            "validation_digest": api.VALIDATION_DIGEST,
            "opinion_digest": "sha256:" + "b" * 64,
            "benchmark": {"case_count": 160},
            "primary_disagreement_count": 48,
            "joint_primary_unknown_count": 0,
            "recent_disagreement_count": 0,
            "gate_calculation": {"disposition": "REVISE_TARGET_OR_PARK", "reasons": ["test"]},
        }

    fake_verified = {
        "manifest_sha256": prepare["manifest_sha256"],
        "selection_digest": prepare["selection_digest"],
        "evidence_digest": prepare["evidence_digest"],
        "selected_case_count": 160,
        "source_isolation_verified": True,
    }
    fake_opinions = [{"case_id": item["case_id"]} for item in decisions for _ in range(2)]
    with (
        patch.object(api, "find_project_root", return_value=ROOT),
        patch.object(api, "load_config", return_value=config),
        patch.object(api, "verify_prepare_artifact", return_value=fake_verified),
        patch.object(api, "_object", side_effect=object_side_effect),
        patch.object(api, "digest", return_value=prepare["evidence_digest"]),
        patch.object(api, "_parse_decisions", return_value=(decisions_raw, decisions)),
        patch.object(api, "build_opinions", return_value=fake_opinions),
        patch.object(api, "publish_bundle", side_effect=publish_side_effect),
        patch.object(api, "score_sealed_bundle", side_effect=score_side_effect),
        patch.object(api, "verify_resolve_score_artifact", return_value={"verified": True}),
    ):
        # The path-boundary details are covered by production integration; this test owns ordering.
        result = api.run_resolve_score(
            config_path=ROOT / api.DEFAULT_CONFIG,
            training_root=tmp_path,
            output_root=output,
            code_revision="f" * 40,
        )
    assert result == {"verified": True}
    assert events == ["seal", "score"]
