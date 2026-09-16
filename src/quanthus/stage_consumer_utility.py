"""P033 bounded consumer-utility packet preparation over the exact P029 population.

The module changes no market semantics. It reconstructs the already accepted P029 retrospective
replay from the same immutable F9 artifacts and deterministically prepares blinded Facts-only versus
Facts+Layered consumer packets. It does not call an evaluator and makes no PIT, predictive,
economic, strategy or trading claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_proposition_bundle_replay import build_real_data_replay, load_replay_request
from quanthus.structure_v2_inputs import canonical, digest

REQUEST_PATH = "config/stage-learning/stage-consumer-utility-prereg.v1.json"
REQUEST_SCHEMA = "quanthus.stage-consumer-utility-prereg.v1"
MANIFEST_SCHEMA = "quanthus.stage-consumer-utility-packet-artifact.v1"
FILES = frozenset({"packets.jsonl", "assignments.json", "answer-key.json", "summary.json"})


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _event_root(value: Path | None = None) -> Path:
    return (value or Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))).resolve()


def load_consumer_utility_request(path: Path) -> dict[str, Any]:
    raw = _load(path)
    required = {
        "schema_version",
        "work_item",
        "state",
        "effective_date",
        "question",
        "bindings",
        "population",
        "frozen_semantics",
        "arms",
        "consumer_roles",
        "assignment",
        "evaluator_contract",
        "questions",
        "answer_key_authority",
        "critical_error_definition",
        "metrics",
        "decision_rule",
        "resource_budget",
        "stop_conditions",
        "claim_ceiling",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("P033 preregistration fields differ")
    if (
        raw["schema_version"] != REQUEST_SCHEMA
        or raw["work_item"] != "P3-STAGE-033"
        or raw["state"] != "PREREGISTERED_CONSUMER_UTILITY_ONLY"
    ):
        raise ValueError("P033 preregistration identity differs")

    root = find_project_root()
    bindings = raw["bindings"]
    expected_bindings = {
        "continuous_facts_preference",
        "p029_result",
        "p032_stop_review",
        "p033_route_review",
    }
    if not isinstance(bindings, dict) or set(bindings) != expected_bindings:
        raise ValueError("P033 bindings differ")
    for binding in bindings.values():
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise ValueError("P033 binding shape differs")
        target = (root / binding["path"]).resolve()
        if root.resolve() not in target.parents or _sha(target) != binding["sha256"]:
            raise ValueError("P033 binding digest differs")

    population = raw["population"]
    if (
        population.get("authority") != "exact_p029_population"
        or population.get("observation_count") != 10
        or population.get("stage_10_only_count") != 4
        or population.get("stage_10_and_11_count") != 6
        or population.get("sampling") is not False
        or population.get("replacement") is not False
        or population.get("new_market_or_dwd_read") is not False
        or population.get("protected_2022_2024_access") is not False
        or population.get("permanent_blind_2025_access") is not False
    ):
        raise ValueError("P033 population differs")

    semantics = raw["frozen_semantics"]
    if semantics != {
        "stage_10_persistence_sessions": 5,
        "stage_10_failure_depth_abs_min": "0.02",
        "stage_11_requires_prior_stage_10_reference": True,
        "stage_11_same_support_must_remain_unreclaimed": True,
        "stage_11_rebound_min": "0.05",
        "primary_stage": None,
        "development_shadow_only": True,
    }:
        raise ValueError("P033 Stage10/11 semantics differ")

    roles = raw["consumer_roles"]
    if roles != [
        "short_term_event_driven",
        "fund_midterm_risk",
        "quant_research",
        "data_governance",
    ]:
        raise ValueError("P033 consumer roles differ")
    assignment = raw["assignment"]
    if assignment.get("rule") != (
        "facts_only when (observation_index + role_index) % 2 == 0, facts_plus_layered otherwise"
    ):
        raise ValueError("P033 assignment rule differs")
    if assignment.get("properties") != {
        "evaluations_total": 40,
        "evaluations_per_arm": 20,
        "evaluations_per_role": 10,
        "evaluations_per_role_per_arm": 5,
        "evaluations_per_observation_per_arm": 2,
        "same_role_sees_same_observation_in_both_arms": False,
    }:
        raise ValueError("P033 assignment properties differ")

    evaluator = raw["evaluator_contract"]
    if (
        evaluator.get("isolated_session_per_role") is not True
        or evaluator.get("same_exact_model_and_provider_across_roles") is not True
        or evaluator.get("arm_label_hidden_from_evaluator") is not True
        or evaluator.get("answer_key_hidden_until_all_role_outputs_are_sealed") is not True
        or evaluator.get("evaluator_unavailable_behavior")
        != "BLOCK_BEFORE_EXPERIMENT_NO_HOST_SUBSTITUTION"
    ):
        raise ValueError("P033 evaluator contract differs")
    if raw["resource_budget"].get("total_evaluations") != 40:
        raise ValueError("P033 resource budget differs")
    return raw


def _visible_codes(row: dict[str, Any]) -> list[int]:
    shadow = row["development_shadow"]
    assertions = [
        *shadow.get("context_assertions", []),
        *shadow.get("condition_assertions", []),
    ]
    return [int(item["historical_stage_code"]) for item in assertions]


def _packet_id(identity: dict[str, Any], role: str) -> str:
    return digest({"identity": identity, "role": role}).replace("sha256:", "p033_")


def build_consumer_packet_set_from_replay(
    request: dict[str, Any], replay: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if len(replay) != request["population"]["observation_count"]:
        raise ValueError("P033 replay population count differs")
    rows = sorted(
        replay,
        key=lambda row: (
            row["observation_identity"]["market_date"],
            row["observation_identity"]["security_code"],
            row["observation_identity"]["process_id"],
        ),
    )
    identities = [canonical(row["observation_identity"]) for row in rows]
    if len(set(identities)) != len(rows):
        raise ValueError("P033 observation identities are not unique")
    nested_count = sum(_visible_codes(row) == [10, 11] for row in rows)
    if nested_count != request["population"]["stage_10_and_11_count"]:
        raise ValueError("P033 nested population differs")
    if any(_visible_codes(row) not in ([10], [10, 11]) for row in rows):
        raise ValueError("P033 source replay contains unsupported layered semantics")

    semantics = request["frozen_semantics"]
    question_defs = request["questions"]
    packets: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    answers: dict[str, Any] = {}
    roles = request["consumer_roles"]

    for observation_index, row in enumerate(rows):
        visible = _visible_codes(row)
        nested = visible == [10, 11]
        expected_interpretation = (
            "structural_failure_with_nested_rebound" if nested else "structural_failure_only"
        )
        for role_index, role in enumerate(roles):
            arm = (
                "facts_only"
                if (observation_index + role_index) % 2 == 0
                else "facts_plus_layered"
            )
            packet_id = _packet_id(row["observation_identity"], role)
            packet = {
                "schema_version": "quanthus.stage-consumer-utility-packet.v1",
                "packet_id": packet_id,
                "consumer_role": role,
                "observation_identity": row["observation_identity"],
                "knowledge_boundary": {
                    "source_view": "retrospective",
                    "strict_pit_claim": False,
                    "prediction_or_economic_task": False,
                },
                "continuous_facts": row["facts_only"],
                "semantic_definitions": {
                    "stage_10": {
                        "meaning": "persistent structural-failure context",
                        "persistence_sessions": semantics["stage_10_persistence_sessions"],
                        "failure_depth_abs_min": semantics["stage_10_failure_depth_abs_min"],
                    },
                    "stage_11": {
                        "meaning": "nested rebound condition inside confirmed Stage10 context",
                        "requires_prior_stage_10_reference": semantics[
                            "stage_11_requires_prior_stage_10_reference"
                        ],
                        "same_support_must_remain_unreclaimed": semantics[
                            "stage_11_same_support_must_remain_unreclaimed"
                        ],
                        "rebound_min": semantics["stage_11_rebound_min"],
                    },
                },
                "questions": question_defs,
            }
            if arm == "facts_plus_layered":
                packet["layered_overlay"] = {
                    "visible_historical_stage_codes": visible,
                    "primary_stage": row["development_shadow"].get("primary_stage"),
                    "surface": "development_shadow",
                }
            packets.append(packet)
            assignments.append(
                {
                    "packet_id": packet_id,
                    "observation_index": observation_index,
                    "role_index": role_index,
                    "consumer_role": role,
                    "arm": arm,
                }
            )
            answers[packet_id] = {
                "Q1_PARENT_ACTIVE": True,
                "Q2_NESTED_REBOUND_ACTIVE": nested,
                "Q3_PARENT_RESOLVED_BY_REBOUND": False,
                "Q4_CANONICAL_INTERPRETATION": expected_interpretation,
            }

    for role in roles:
        role_assignments = [item for item in assignments if item["consumer_role"] == role]
        if len(role_assignments) != 10 or {item["arm"] for item in role_assignments} != {
            "facts_only",
            "facts_plus_layered",
        }:
            raise ValueError("P033 role assignment is incomplete")
        if sum(item["arm"] == "facts_only" for item in role_assignments) != 5:
            raise ValueError("P033 role arm balance differs")
    for observation_index in range(10):
        obs_assignments = [
            item for item in assignments if item["observation_index"] == observation_index
        ]
        if sum(item["arm"] == "facts_only" for item in obs_assignments) != 2:
            raise ValueError("P033 observation arm balance differs")

    packets.sort(key=lambda item: (item["consumer_role"], digest(item["packet_id"])))
    assignments.sort(key=lambda item: item["packet_id"])
    summary = {
        "schema_version": "quanthus.stage-consumer-utility-packet-summary.v1",
        "work_item": request["work_item"],
        "observation_count": len(rows),
        "packet_count": len(packets),
        "facts_only_packet_count": sum(item["arm"] == "facts_only" for item in assignments),
        "facts_plus_layered_packet_count": sum(
            item["arm"] == "facts_plus_layered" for item in assignments
        ),
        "consumer_role_count": len(roles),
        "answer_key_count": len(answers),
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "future_outcome_reads": 0,
        "evaluator_calls": 0,
        "experiment_result_available": False,
        "claim_ceiling": request["claim_ceiling"],
    }
    return packets, assignments, answers, summary


def build_consumer_packet_artifact_payloads(
    request: dict[str, Any], *, event_root: Path | None = None
) -> tuple[dict[str, bytes], dict[str, Any]]:
    root = find_project_root()
    p029_result = _load(root / request["bindings"]["p029_result"]["path"])
    protocol_path = str(p029_result["protocol"]["path"])
    replay_request = load_replay_request(root / protocol_path)
    replay_payloads, replay_summary = build_real_data_replay(
        replay_request, event_root=_event_root(event_root)
    )
    if (
        replay_summary["observation_count"] != 10
        or replay_summary["nested_10_11_count"] != 6
        or replay_summary["market_data_reads"] != 0
        or replay_summary["dwd_provider_opens"] != 0
        or replay_summary["strict_point_in_time_claim"] is not False
    ):
        raise ValueError("P033 source P029 replay boundary differs")
    replay = json.loads(replay_payloads["replay.json"])
    packets, assignments, answers, summary = build_consumer_packet_set_from_replay(request, replay)
    packet_lines = b"".join(canonical(item) + b"\n" for item in packets)
    payloads = {
        "packets.jsonl": packet_lines,
        "assignments.json": canonical(assignments),
        "answer-key.json": canonical(answers),
        "summary.json": canonical(summary),
    }
    return payloads, summary


def write_consumer_packet_artifact(
    output: Path, request: dict[str, Any], *, code_revision: str
) -> None:
    payloads, summary = build_consumer_packet_artifact_payloads(request)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "code_revision": code_revision,
        "request_digest": digest(request),
        "research_only": True,
        "execution_authorized": False,
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "experiment_result_available": False,
        "summary_digest": digest(summary),
        "files": {
            name: "sha256:" + hashlib.sha256(payload).hexdigest()
            for name, payload in payloads.items()
        },
    }
    all_payloads = {**payloads, "manifest.json": canonical(manifest)}
    all_payloads["READY"] = canonical(
        {
            "schema_version": "quanthus.stage-consumer-utility-packet-ready.v1",
            "manifest_sha256": "sha256:"
            + hashlib.sha256(all_payloads["manifest.json"]).hexdigest(),
        }
    )
    for name, payload in all_payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def verify_consumer_packet_artifact(
    root: Path, *, code_revision: str, request_digest: str
) -> dict[str, Any]:
    root = root.resolve()
    expected = FILES | {"manifest.json", "READY"}
    if not root.is_dir() or {item.name for item in root.iterdir()} != expected:
        raise ValueError("P033 packet artifact file set differs")
    if any(item.is_symlink() or not item.is_file() for item in root.iterdir()):
        raise ValueError("P033 packet artifact contains indirect members")
    manifest = _load(root / "manifest.json")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("code_revision") != code_revision
        or manifest.get("request_digest") != request_digest
        or manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
        or manifest.get("knowledge_view") != "retrospective"
        or manifest.get("strict_pit_claim") is not False
        or manifest.get("experiment_result_available") is not False
        or set(manifest.get("files") or {}) != FILES
    ):
        raise ValueError("P033 packet manifest differs")
    for name in FILES:
        observed = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest["files"][name] != observed:
            raise ValueError("P033 packet payload digest differs")
    ready_expected = {
        "schema_version": "quanthus.stage-consumer-utility-packet-ready.v1",
        "manifest_sha256": "sha256:"
        + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
    }
    if _load(root / "READY") != ready_expected:
        raise ValueError("P033 packet READY binding differs")

    project_root = find_project_root()
    request = load_consumer_utility_request(project_root / REQUEST_PATH)
    if digest(request) != request_digest:
        raise ValueError("P033 packet request digest differs")
    rebuilt, summary = build_consumer_packet_artifact_payloads(request)
    for name in FILES:
        if rebuilt[name] != (root / name).read_bytes():
            raise ValueError("P033 packet deep reconstruction differs")
    if digest(summary) != manifest["summary_digest"]:
        raise ValueError("P033 packet summary digest differs")

    packets = [json.loads(line) for line in (root / "packets.jsonl").read_bytes().splitlines()]
    assignments = _load(root / "assignments.json")
    answers = _load(root / "answer-key.json")
    return {
        "artifact_ref": str(root),
        "manifest_sha256": ready_expected["manifest_sha256"],
        "request_digest": request_digest,
        "packet_count": len(packets),
        "assignment_count": len(assignments),
        "answer_key_count": len(answers),
        "facts_only_packet_count": summary["facts_only_packet_count"],
        "facts_plus_layered_packet_count": summary["facts_plus_layered_packet_count"],
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "experiment_result_available": False,
        "review_packets": packets,
        "assignments": assignments,
        "scoring_key": answers,
        "execution_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    if Path(args.request).as_posix() != REQUEST_PATH:
        raise ValueError("P033 packet preparation requires fixed governed request")
    if len(args.code_revision) != 40 or any(
        char not in "0123456789abcdef" for char in args.code_revision
    ):
        raise ValueError("P033 packet preparation requires exact lowercase code revision")
    request = load_consumer_utility_request(find_project_root() / REQUEST_PATH)
    output = Path(args.output)
    write_consumer_packet_artifact(output, request, code_revision=args.code_revision)
    verified = verify_consumer_packet_artifact(
        output, code_revision=args.code_revision, request_digest=digest(request)
    )
    hidden = {"review_packets", "assignments", "scoring_key"}
    safe = {key: value for key, value in verified.items() if key not in hidden}
    print(json.dumps(safe, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
