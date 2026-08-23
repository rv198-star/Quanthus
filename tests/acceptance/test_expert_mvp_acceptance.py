from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.expert_evaluation.contracts import ExpertLifecycleDecision
from quanthus.expert_evaluation.lifecycle import (
    ExpertLifecycleError,
    acceptance_packet_digest,
    advance_product_binding,
    publish_lifecycle_decision,
    verify_acceptance_packet,
)
from quanthus.expert_evaluation.serialization import artifact_from_bytes, digest_value
from quanthus.policy import find_project_root
from tests.expert_evaluation_fixtures import build_evaluation_graph

ROOT = find_project_root()
PACKET = (
    ROOT
    / "config/expert-evaluations/acceptance/candidate-p4-trend-risk-1.0.1.v1.json"
)
DECISION = (
    ROOT
    / "config/expert-evaluations/lifecycle/"
    "candidate-p4-trend-risk-1.0.1.inconclusive.v1.json"
)
ARTIFACT_ROOT = Path(os.environ.get("QUANTHUS_TEST_EXPERT_ARTIFACT_ROOT", "/nonexistent"))


def test_acceptance_packet_and_human_decision_are_deterministic() -> None:
    packet = json.loads(PACKET.read_bytes())
    assert packet["digest"] == acceptance_packet_digest(packet)
    assert packet["strategy"]["strategy_digest"] == (
        "sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c"
    )
    assert all(item["sealed_access"] is False for item in packet["read_audit"])
    assert packet["non_authority"] == {
        "execution_authorized": False,
        "fill": False,
        "notification": False,
        "order": False,
        "promotion": False,
    }

    decision = artifact_from_bytes(DECISION.read_bytes())
    assert isinstance(decision, ExpertLifecycleDecision)
    assert decision.decision.value == "inconclusive"
    assert decision.actor_type.value == "human"
    assert decision.shadow_disposition.status.value == "disabled"
    assert decision.shadow_disposition.observation_budget is None
    assert decision.package_digest == packet["digest"]


@pytest.mark.skipif(not ARTIFACT_ROOT.is_dir(), reason="production expert artifacts not mounted")
def test_complete_packet_verifies_independent_reproductions_and_zero_sealed_access() -> None:
    packet = verify_acceptance_packet(PACKET, ARTIFACT_ROOT)
    assert packet["reproductions"]["semantic_match"] is True
    assert packet["stop_condition_disposition"]["determinism"] == "passed"


def test_acceptance_packet_rejects_a_sealed_read_before_artifact_access(
    tmp_path: Path,
) -> None:
    packet = json.loads(PACKET.read_bytes())
    packet["read_audit"][0]["last_date"] = "2022-01-01"
    packet["digest"] = acceptance_packet_digest(packet)
    path = tmp_path / "sealed.json"
    from quanthus.expert_evaluation.serialization import canonical_json_bytes

    path.write_bytes(canonical_json_bytes(packet))
    with pytest.raises(ExpertLifecycleError, match="sealed_partition_access"):
        verify_acceptance_packet(path, tmp_path)


def test_lifecycle_append_is_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    decision = artifact_from_bytes(DECISION.read_bytes())
    assert isinstance(decision, ExpertLifecycleDecision)
    path = tmp_path / "decision.json"
    assert publish_lifecycle_decision(path, decision) is True
    assert publish_lifecycle_decision(path, decision) is False

    conflict = replace(decision, rationale="A different human rationale.")
    with pytest.raises(ExpertLifecycleError, match="lifecycle_append_conflict"):
        publish_lifecycle_decision(path, conflict)
    assert path.read_bytes() == decision.canonical_bytes()


def test_serving_advance_preserves_old_package_and_moves_only_exact_binding(
    tmp_path: Path,
) -> None:
    current = json.loads((ROOT / "config/product-expert-evidence.v1.json").read_bytes())
    old_package, decided_package = current["packages"]
    source = {
        **current,
        "bindings": [
            {
                **current["bindings"][0],
                "package_id": old_package["package_id"],
                "package_digest": old_package["package_digest"],
            }
        ],
        "packages": [old_package],
    }
    path = tmp_path / "product.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    observed = advance_product_binding(
        path,
        package=decided_package,
        expected_previous_digest=old_package["package_digest"],
    )
    result = json.loads(path.read_bytes())
    assert observed == decided_package["package_digest"]
    assert result["packages"] == [old_package, decided_package]
    assert result["bindings"][0]["package_digest"] == decided_package["package_digest"]

    changed = dict(decided_package)
    changed["package_id"] = "different"
    changed.pop("package_digest")
    changed["package_digest"] = digest_value(changed)
    with pytest.raises(ExpertLifecycleError, match="serving_advance_conflict"):
        advance_product_binding(
            path,
            package=changed,
            expected_previous_digest=old_package["package_digest"],
        )


def test_contract_rejects_keep_with_disabled_shadow() -> None:
    graph = build_evaluation_graph()
    with pytest.raises(Exception, match="keep requires enabled Shadow"):
        replace(
            graph.lifecycle,
            shadow_disposition=replace(
                graph.lifecycle.shadow_disposition,
                status=type(graph.lifecycle.shadow_disposition.status).DISABLED,
                observation_budget=None,
            ),
        )
