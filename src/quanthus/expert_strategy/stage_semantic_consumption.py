"""P3-EXPERT-006 bounded Stage10/11 expert-consumption discovery.

This module reuses the exact accepted P029 retrospective population and prepares one immutable
research packet per expert role.  It performs no new market/DWD read, does not change Stage
semantics, and grants no score, veto, constraint, strategy, execution, or trading authority.
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

REQUEST_PATH = "config/expert-strategies/stage10-11-consumption-discovery.v1.json"
REQUEST_SCHEMA = "quanthus.stage-expert-consumption-discovery-prereg.v1"
MANIFEST_SCHEMA = "quanthus.stage-expert-consumption-packet-artifact.v1"
FILES = frozenset({"role-packets.jsonl", "semantic-key.json", "summary.json"})


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _event_root(value: Path | None = None) -> Path:
    return (value or Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))).resolve()


def load_stage_expert_consumption_request(path: Path) -> dict[str, Any]:
    raw = _load(path)
    required = {
        "schema_version",
        "work_item",
        "state",
        "effective_date",
        "question",
        "bindings",
        "population",
        "semantic_surface",
        "consumer_roles",
        "packet_contract",
        "evaluator_contract",
        "response_contract",
        "metrics",
        "decision_rule",
        "resource_budget",
        "stop_conditions",
        "automatic_successor",
        "claim_ceiling",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("P3-EXPERT-006 preregistration fields differ")
    if (
        raw["schema_version"] != REQUEST_SCHEMA
        or raw["work_item"] != "P3-EXPERT-006"
        or raw["state"] != "PREREGISTERED_STAGE10_11_CONSUMPTION_DISCOVERY"
    ):
        raise ValueError("P3-EXPERT-006 preregistration identity differs")

    root = find_project_root()
    bindings = raw["bindings"]
    expected_bindings = {
        "direction_review",
        "phase1_closeout",
        "p029_result",
        "p033_result",
        "expert_layer_design",
    }
    if not isinstance(bindings, dict) or set(bindings) != expected_bindings:
        raise ValueError("P3-EXPERT-006 bindings differ")
    for binding in bindings.values():
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise ValueError("P3-EXPERT-006 binding shape differs")
        target = (root / binding["path"]).resolve()
        if root.resolve() not in target.parents or _sha(target) != binding["sha256"]:
            raise ValueError("P3-EXPERT-006 binding digest differs")

    population = raw["population"]
    if population != {
        "authority": "exact_p029_population",
        "observation_count": 10,
        "stage_10_only_count": 4,
        "stage_10_and_11_count": 6,
        "sampling": False,
        "replacement": False,
        "new_market_or_dwd_read": False,
        "protected_2022_2024_access": False,
        "permanent_blind_2025_access": False,
        "future_outcome_read": False,
        "knowledge_view": "retrospective",
    }:
        raise ValueError("P3-EXPERT-006 population differs")

    surface = raw["semantic_surface"]
    if (
        surface.get("facts_authority") != "authoritative_substrate"
        or surface.get("surface") != "development_shadow"
        or surface.get("accepted_historical_stage_codes") != [10, 11]
        or surface.get("stage_11_requires_prior_stage_10_reference") is not True
        or surface.get("primary_stage") is not None
        or surface.get("advisory_only") is not True
        or any(
            surface.get(key) is not False
            for key in (
                "action_authority",
                "score_authority",
                "veto_authority",
                "constraint_authority",
                "strategy_authority",
                "trading_authority",
            )
        )
    ):
        raise ValueError("P3-EXPERT-006 semantic authority differs")

    roles = raw["consumer_roles"]
    if roles != [
        "short_term_event_driven",
        "fund_midterm_risk",
        "quant_research",
        "data_governance",
    ]:
        raise ValueError("P3-EXPERT-006 consumer roles differ")
    packet = raw["packet_contract"]
    if packet != {
        "one_packet_per_role": True,
        "observations_per_packet": 10,
        "role_packet_count": 4,
        "same_observation_set_for_all_roles": True,
        "facts_and_layered_surface_visible": True,
        "stage_code_expansion_forbidden": True,
        "buy_sell_or_position_recommendation_forbidden": True,
    }:
        raise ValueError("P3-EXPERT-006 packet contract differs")
    evaluator = raw["evaluator_contract"]
    if (
        evaluator.get("isolated_session_per_role") is not True
        or evaluator.get("same_exact_model_and_provider_across_roles") is not True
        or evaluator.get("other_role_outputs_hidden_until_all_outputs_sealed") is not True
        or evaluator.get("semantic_key_hidden_until_all_outputs_sealed") is not True
        or evaluator.get("result_retries_per_role") != 0
    ):
        raise ValueError("P3-EXPERT-006 evaluator contract differs")
    if raw["automatic_successor"] is not None:
        raise ValueError("P3-EXPERT-006 cannot pre-authorize a successor")
    return raw


def _visible_codes(row: dict[str, Any]) -> list[int]:
    shadow = row["development_shadow"]
    assertions = [
        *shadow.get("context_assertions", []),
        *shadow.get("condition_assertions", []),
    ]
    return [int(item["historical_stage_code"]) for item in assertions]


def _packet_id(role: str, identities: list[dict[str, Any]]) -> str:
    return digest({"work_item": "P3-EXPERT-006", "role": role, "identities": identities}).replace(
        "sha256:", "p3expert006_"
    )


def build_role_packets_from_replay(
    request: dict[str, Any], replay: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if len(replay) != request["population"]["observation_count"]:
        raise ValueError("P3-EXPERT-006 replay population count differs")
    rows = sorted(
        replay,
        key=lambda row: (
            row["observation_identity"]["market_date"],
            row["observation_identity"]["security_code"],
            row["observation_identity"]["process_id"],
        ),
    )
    identities = [row["observation_identity"] for row in rows]
    if len({canonical(item) for item in identities}) != len(rows):
        raise ValueError("P3-EXPERT-006 observation identities are not unique")
    visible_codes = [_visible_codes(row) for row in rows]
    if any(item not in ([10], [10, 11]) for item in visible_codes):
        raise ValueError("P3-EXPERT-006 source replay contains unsupported semantics")
    if sum(item == [10, 11] for item in visible_codes) != 6:
        raise ValueError("P3-EXPERT-006 nested observation count differs")

    semantic_key = {
        str(index): {
            "observation_identity": row["observation_identity"],
            "parent_context_active": True,
            "nested_rebound_active": visible_codes[index] == [10, 11],
        }
        for index, row in enumerate(rows)
    }
    packets: list[dict[str, Any]] = []
    for role in request["consumer_roles"]:
        observations = []
        for index, row in enumerate(rows):
            observations.append(
                {
                    "observation_index": index,
                    "observation_identity": row["observation_identity"],
                    "knowledge_boundary": {
                        "source_view": "retrospective",
                        "strict_pit_claim": False,
                        "prediction_or_economic_task": False,
                    },
                    "continuous_facts": row["facts_only"],
                    "stage_semantic_surface": row["development_shadow"],
                }
            )
        packets.append(
            {
                "schema_version": "quanthus.stage-expert-consumption-role-packet.v1",
                "packet_id": _packet_id(role, identities),
                "consumer_role": role,
                "advisory_contract": {
                    "facts_authority": "authoritative_substrate",
                    "semantic_surface": "development_shadow",
                    "semantic_surface_authority": "development_advisory_only",
                    "score_authority": False,
                    "veto_authority": False,
                    "constraint_authority": False,
                    "action_authority": False,
                    "strategy_authority": False,
                    "trading_authority": False,
                    "primary_stage": None,
                },
                "observations": observations,
                "response_contract": request["response_contract"],
                "gap_trigger": request["decision_rule"][
                    "SPECIFIC_DOWNSTREAM_SEMANTIC_GAP_IDENTIFIED"
                ],
                "claim_ceiling": request["claim_ceiling"],
            }
        )
    packets.sort(key=lambda item: item["consumer_role"])
    summary = {
        "schema_version": "quanthus.stage-expert-consumption-packet-summary.v1",
        "work_item": request["work_item"],
        "observation_count": len(rows),
        "role_packet_count": len(packets),
        "role_observation_evaluation_count": len(rows) * len(packets),
        "stage_10_only_count": sum(item == [10] for item in visible_codes),
        "stage_10_and_11_count": sum(item == [10, 11] for item in visible_codes),
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "future_outcome_reads": 0,
        "evaluator_calls": 0,
        "execution_authorized": False,
        "automatic_successor": None,
        "claim_ceiling": request["claim_ceiling"],
    }
    return packets, semantic_key, summary


def build_stage_expert_consumption_payloads(
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
        raise ValueError("P3-EXPERT-006 source P029 replay boundary differs")
    replay = json.loads(replay_payloads["replay.json"])
    packets, semantic_key, summary = build_role_packets_from_replay(request, replay)
    payloads = {
        "role-packets.jsonl": b"".join(canonical(item) + b"\n" for item in packets),
        "semantic-key.json": canonical(semantic_key),
        "summary.json": canonical(summary),
    }
    return payloads, summary


def write_stage_expert_consumption_artifact(
    output: Path, request: dict[str, Any], *, code_revision: str
) -> None:
    payloads, summary = build_stage_expert_consumption_payloads(request)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "code_revision": code_revision,
        "request_digest": digest(request),
        "research_only": True,
        "execution_authorized": False,
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "semantic_key_sealed_from_evaluators": True,
        "summary_digest": digest(summary),
        "files": {
            name: "sha256:" + hashlib.sha256(payload).hexdigest()
            for name, payload in payloads.items()
        },
    }
    all_payloads = {**payloads, "manifest.json": canonical(manifest)}
    all_payloads["READY"] = canonical(
        {
            "schema_version": "quanthus.stage-expert-consumption-ready.v1",
            "manifest_sha256": "sha256:"
            + hashlib.sha256(all_payloads["manifest.json"]).hexdigest(),
        }
    )
    for name, payload in all_payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def verify_stage_expert_consumption_artifact(
    root: Path, *, code_revision: str, request_digest: str
) -> dict[str, Any]:
    root = root.resolve()
    expected = FILES | {"manifest.json", "READY"}
    if not root.is_dir() or {item.name for item in root.iterdir()} != expected:
        raise ValueError("P3-EXPERT-006 artifact file set differs")
    if any(item.is_symlink() or not item.is_file() for item in root.iterdir()):
        raise ValueError("P3-EXPERT-006 artifact contains indirect members")
    manifest = _load(root / "manifest.json")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("code_revision") != code_revision
        or manifest.get("request_digest") != request_digest
        or manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
        or manifest.get("knowledge_view") != "retrospective"
        or manifest.get("strict_pit_claim") is not False
        or manifest.get("semantic_key_sealed_from_evaluators") is not True
        or set(manifest.get("files") or {}) != FILES
    ):
        raise ValueError("P3-EXPERT-006 artifact manifest differs")
    for name in FILES:
        observed = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
        if manifest["files"][name] != observed:
            raise ValueError("P3-EXPERT-006 artifact digest differs")
    manifest_sha = "sha256:" + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if _load(root / "READY") != {
        "schema_version": "quanthus.stage-expert-consumption-ready.v1",
        "manifest_sha256": manifest_sha,
    }:
        raise ValueError("P3-EXPERT-006 READY binding differs")

    project_root = find_project_root()
    request = load_stage_expert_consumption_request(project_root / REQUEST_PATH)
    if digest(request) != request_digest:
        raise ValueError("P3-EXPERT-006 request digest differs")
    rebuilt, summary = build_stage_expert_consumption_payloads(request)
    for name in FILES:
        if rebuilt[name] != (root / name).read_bytes():
            raise ValueError("P3-EXPERT-006 deep reconstruction differs")
    if digest(summary) != manifest["summary_digest"]:
        raise ValueError("P3-EXPERT-006 summary digest differs")
    packets = [json.loads(line) for line in (root / "role-packets.jsonl").read_bytes().splitlines()]
    return {
        "artifact_ref": str(root),
        "manifest_sha256": manifest_sha,
        "request_digest": request_digest,
        "role_packet_count": len(packets),
        "observation_count": summary["observation_count"],
        "role_observation_evaluation_count": summary["role_observation_evaluation_count"],
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "future_outcome_reads": 0,
        "execution_authorized": False,
        "automatic_successor": None,
        "review_packets": packets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    if Path(args.request).as_posix() != REQUEST_PATH:
        raise ValueError("P3-EXPERT-006 requires fixed governed request")
    if len(args.code_revision) != 40 or any(
        char not in "0123456789abcdef" for char in args.code_revision
    ):
        raise ValueError("P3-EXPERT-006 requires exact lowercase code revision")
    request = load_stage_expert_consumption_request(find_project_root() / REQUEST_PATH)
    output = Path(args.output)
    write_stage_expert_consumption_artifact(output, request, code_revision=args.code_revision)
    verified = verify_stage_expert_consumption_artifact(
        output, code_revision=args.code_revision, request_digest=digest(request)
    )
    safe = {key: value for key, value in verified.items() if key != "review_packets"}
    print(json.dumps(safe, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
