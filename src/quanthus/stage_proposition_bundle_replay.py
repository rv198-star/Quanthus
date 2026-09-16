"""P029 bounded real-history Stage10/11 proposition consumer replay.

This replay consumes only the two immutable accepted F9 artifacts already governed by
P3-STAGE-002. The source record view is retrospective. The module therefore evaluates
representation/consumer semantics only; it does not establish PIT, predictive, economic,
strategy or trading utility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_projection_artifact import (
    _reconstruct_source_facts,
    build_projection_qualification,
    load_projection_qualification_request,
)
from quanthus.stage_proposition_bundle import (
    build_bundle,
    consumer_view,
    load_stage_proposition_contract,
    make_assertion,
)
from quanthus.structure_v2_inputs import canonical, digest

REQUEST_PATH = "config/stage-learning/stage-proposition-bundle-real-data-replay.v1.json"
REQUEST_SCHEMA = "quanthus.stage-proposition-bundle-real-data-replay.v1"
RESULT_SCHEMA = "quanthus.stage-proposition-bundle-real-data-replay-result.v1"
MANIFEST_SCHEMA = "quanthus.stage-proposition-bundle-real-data-replay-artifact.v1"
FILES = frozenset({"replay.json", "summary.json", "runtime.json"})


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _event_root(value: Path | None = None) -> Path:
    return (value or Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))).resolve()


def load_replay_request(path: Path) -> dict[str, Any]:
    raw = _load(path)
    required = {
        "schema_version",
        "replay_id",
        "work_item",
        "state",
        "bindings",
        "source_f9",
        "population",
        "views",
        "interpretation_target",
        "knowledge_boundary",
        "authority",
        "acceptance",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("P029 replay request fields differ")
    if (
        raw["schema_version"] != REQUEST_SCHEMA
        or raw["replay_id"] != "stock-stage-10-11-real-history-consumer-replay-v1"
        or raw["work_item"] != "P3-STAGE-029"
        or raw["state"] != "FROZEN_BOUNDED_REAL_HISTORY_REPLAY_RETROSPECTIVE_NOT_PIT"
    ):
        raise ValueError("P029 replay request identity differs")

    root = find_project_root()
    bindings = raw["bindings"]
    expected_binding_names = {
        "p028_bundle_contract",
        "stage_10_11_projection_qualification",
        "stage_10_11_projection_contract",
    }
    if not isinstance(bindings, dict) or set(bindings) != expected_binding_names:
        raise ValueError("P029 replay bindings differ")
    for binding in bindings.values():
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise ValueError("P029 replay binding shape differs")
        bound = (root / binding["path"]).resolve()
        if root.resolve() not in bound.parents or _sha(bound) != binding["sha256"]:
            raise ValueError("P029 replay binding digest differs")

    source = raw["source_f9"]
    qualification = load_projection_qualification_request(
        root / bindings["stage_10_11_projection_qualification"]["path"]
    )
    frozen_source = qualification["source_f9"]
    for key in (
        "code_revision",
        "request_digest",
        "support_facts_digest",
        "legacy_projection_digest",
        "run_ids",
    ):
        if source.get(key) != frozen_source[key]:
            raise ValueError("P029 replay source identity differs from accepted qualification")
    if source.get("knowledge_view") != "retrospective":
        raise ValueError("P029 replay must preserve retrospective knowledge view")
    counts = source.get("expected_projection_counts")
    if counts != {
        "support_process_observation_count": 241,
        "stage_10_context_count": 10,
        "stage_11_rebound_condition_count": 6,
        "context_only_count": 4,
    }:
        raise ValueError("P029 replay expected population differs")

    population = raw["population"]
    if population != {
        "selection": "all_formal_stage_10_context_true_observations",
        "sampling": False,
        "expected_row_count": 10,
        "same_observation_identity_across_all_views": True,
        "future_outcome_selection": False,
    }:
        raise ValueError("P029 replay population contract differs")
    if raw["views"]["counterfactual_single_code"].get("policy") != (
        "child_precedence_stage_11_else_stage_10"
    ):
        raise ValueError("P029 replay single-code comparator differs")
    if raw["views"]["layered_bundle"].get("surface") != "development_shadow":
        raise ValueError("P029 replay shadow surface differs")

    boundary = raw["knowledge_boundary"]
    if (
        boundary.get("strict_point_in_time_claim_authorized") is not False
        or boundary.get("strict_pit_gate") != "BLOCKED_BY_RETROSPECTIVE_SOURCE_RECORD_VIEW"
        or boundary.get("retrospective_real_history_replay_authorized") is not True
    ):
        raise ValueError("P029 replay knowledge boundary differs")
    authority = raw["authority"]
    required_false = (
        "new_market_data_read_authorized",
        "dwd_provider_open_authorized",
        "future_outcome_access_authorized",
        "consumed_2022_2024_partition_access_authorized",
        "preserved_2025_partition_access_authorized",
        "new_reference_opinion_authorized",
        "candidate_or_threshold_search_authorized",
        "vocabulary_expansion_authorized",
        "primary_stage_publication_authorized",
        "production_expert_consumption_authorized",
        "strategy_consumption_authorized",
        "trading_authorized",
    )
    if authority.get("immutable_f9_artifact_read_authorized") is not True or any(
        authority.get(key) is not False for key in required_false
    ):
        raise ValueError("P029 replay authority differs")
    if authority.get("development_shadow_consumption_authorized") is not True:
        raise ValueError("P029 development shadow authority missing")
    if not isinstance(raw["acceptance"], dict) or any(
        value is not True for value in raw["acceptance"].values()
    ):
        raise ValueError("P029 acceptance contract differs")
    return raw


def _fact_ref(revision_id: str) -> str:
    return f"fact:{revision_id}"


def build_real_data_replay(
    request: dict[str, Any], *, event_root: Path | None = None
) -> tuple[dict[str, bytes], dict[str, Any]]:
    root = find_project_root()
    event = _event_root(event_root)
    qualification_request = load_projection_qualification_request(
        root / request["bindings"]["stage_10_11_projection_qualification"]["path"]
    )
    projection_payloads, qualification_summary = build_projection_qualification(
        qualification_request, event_root=event
    )
    formal_a = json.loads(projection_payloads["projection-a.json"])
    formal_b = json.loads(projection_payloads["projection-b.json"])
    if formal_a != formal_b:
        raise ValueError("P029 accepted cold-run formal projections differ")

    run_root = event / "structure-runs"
    first_source = (run_root / request["source_f9"]["run_ids"][0]).resolve()
    if first_source.parent != run_root.resolve():
        raise ValueError("P029 source run escapes governed root")
    facts, _legacy = _reconstruct_source_facts(first_source)
    if facts.digest != request["source_f9"]["support_facts_digest"]:
        raise ValueError("P029 reconstructed source facts differ")

    contract = load_stage_proposition_contract(
        root / request["bindings"]["p028_bundle_contract"]["path"]
    )
    binding_by_process = {item.process_id: item for item in facts.bindings}
    observation_by_key = {
        (item.process_id, item.market_date.isoformat()): item for item in facts.observations
    }
    distance_by_ref = {item.identity.revision_id: item for item in facts.distances}
    rebound_by_ref = {item.identity.revision_id: item for item in facts.rebounds}

    active_rows = [
        item for item in formal_a["observations"] if item["stage_10_context_active"] is True
    ]
    active_rows.sort(
        key=lambda item: (
            item["market_date"],
            item["security_code"],
            item["process_id"],
        )
    )
    if len(active_rows) != request["population"]["expected_row_count"]:
        raise ValueError("P029 Stage10 active population count differs")

    replay: list[dict[str, Any]] = []
    target_opportunities = 0
    single_retained = 0
    single_parent_elided = 0
    layered_retained = 0
    layered_nested_parent_explicit = 0
    layered_mismatch = 0
    primary_non_null = 0

    for formal in active_rows:
        key = (formal["process_id"], formal["market_date"])
        process = observation_by_key.get(key)
        binding = binding_by_process.get(formal["process_id"])
        if process is None or binding is None:
            raise ValueError("P029 formal observation lacks source fact identity")
        if process.revision_id != formal["source_process_revision_ref"]:
            raise ValueError("P029 process revision differs from formal projection")
        distance_ref = formal["source_distance_revision_ref"]
        distance = distance_by_ref.get(distance_ref)
        if distance is None:
            raise ValueError("P029 active context lacks resolved distance fact")
        rebound_ref = formal["source_rebound_revision_ref"]
        rebound = rebound_by_ref.get(rebound_ref) if rebound_ref is not None else None
        nested = formal["stage_11_rebound_condition_active"] is True
        if nested and rebound is None:
            raise ValueError("P029 nested condition lacks rebound fact")

        facts_only = {
            "process_id": formal["process_id"],
            "security_id": binding.security_id,
            "security_code": formal["security_code"],
            "market_date": formal["market_date"],
            "scale": formal["scale"],
            "phase": process.phase,
            "unreclaimed_age_sessions": process.unreclaimed_age_sessions,
            "support_process_revision_ref": process.revision_id,
            "support_distance_revision_ref": distance_ref,
            "support_distance_value": distance.value,
            "post_break_rebound_revision_ref": rebound_ref,
            "post_break_rebound_value": None if rebound is None else rebound.value,
        }
        selected_code = 11 if nested else 10
        single = {
            "schema_version": "quanthus.stage-counterfactual-single-code-view.v1",
            "policy": "child_precedence_stage_11_else_stage_10",
            "selected_code": selected_code,
            "comparison_only": True,
            "primary_stage_authorized": False,
        }

        process_fact = _fact_ref(process.revision_id)
        distance_fact = _fact_ref(distance_ref)
        confirmation_ref = formal["stage_10_confirmation_ref"]
        stage10 = make_assertion(
            contract,
            proposition_id=contract.by_code[10].proposition_id,
            verdict="TRUE",
            reason_codes=["stage_10_context_confirmed"],
            fact_refs=[process_fact, distance_fact],
            provenance_refs=[
                f"stage10_confirmation:{confirmation_ref}",
                f"formal_projection:{qualification_summary['formal_projection_digest']}",
            ],
        )
        assertions = [stage10]
        if nested:
            assert rebound_ref is not None
            stage11 = make_assertion(
                contract,
                proposition_id=contract.by_code[11].proposition_id,
                verdict="TRUE",
                reason_codes=["stage_11_rebound_condition_active"],
                fact_refs=[process_fact, _fact_ref(rebound_ref)],
                provenance_refs=[
                    f"stage10_confirmation:{confirmation_ref}",
                    f"formal_projection:{qualification_summary['formal_projection_digest']}",
                ],
            )
            assertions.append(stage11)
        bundle = build_bundle(
            contract,
            security_id=binding.security_id,
            knowledge_cutoff=distance.business_time.result_as_of.isoformat(),
            control_state="ACTIVE",
            epistemic_status="ASSERTED",
            assertions=assertions,
        )
        shadow = consumer_view(contract, bundle, surface="development_shadow")
        visible_codes = [
            item.historical_stage_code
            for item in (*shadow.context_assertions, *shadow.condition_assertions)
        ]
        expected_codes = [10, 11] if nested else [10]
        if visible_codes != expected_codes or shadow.hidden_assertion_count != 0:
            layered_mismatch += 1
        if bundle.primary_stage is not None or shadow.primary_stage is not None:
            primary_non_null += 1

        opportunities = 2 if nested else 1
        target_opportunities += opportunities
        single_retained += 1
        layered_retained += len(visible_codes)
        if nested:
            single_parent_elided += 1
            if 10 in visible_codes:
                layered_nested_parent_explicit += 1

        replay.append(
            {
                "observation_identity": {
                    "process_id": formal["process_id"],
                    "market_date": formal["market_date"],
                    "security_id": binding.security_id,
                    "security_code": formal["security_code"],
                    "scale": formal["scale"],
                },
                "knowledge_view": "retrospective",
                "strict_pit_claim": False,
                "facts_only": facts_only,
                "counterfactual_single_code": single,
                "layered_bundle": bundle.to_dict(),
                "development_shadow": shadow.to_dict(),
            }
        )

    nested_count = sum(
        row["counterfactual_single_code"]["selected_code"] == 11 for row in replay
    )
    expected = request["source_f9"]["expected_projection_counts"]
    if nested_count != expected["stage_11_rebound_condition_count"]:
        raise ValueError("P029 nested population count differs")
    if len(replay) - nested_count != expected["context_only_count"]:
        raise ValueError("P029 context-only population count differs")
    expected_opportunities = len(replay) + nested_count
    if target_opportunities != expected_opportunities:
        raise ValueError("P029 target opportunity accounting differs")

    summary = {
        "schema_version": RESULT_SCHEMA,
        "replay_id": request["replay_id"],
        "work_item": request["work_item"],
        "source_kind": request["source_f9"]["source_kind"],
        "source_run_ids": request["source_f9"]["run_ids"],
        "source_support_facts_digest": facts.digest,
        "formal_projection_digest": qualification_summary["formal_projection_digest"],
        "p028_contract_digest": contract.contract_digest,
        "knowledge_view": "retrospective",
        "strict_point_in_time_claim": False,
        "strict_pit_gate": "BLOCKED_BY_RETROSPECTIVE_SOURCE_RECORD_VIEW",
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "future_outcome_reads": 0,
        "observation_count": len(replay),
        "context_only_count": len(replay) - nested_count,
        "nested_10_11_count": nested_count,
        "interpretation_metrics": {
            "explicit_target_proposition_opportunity_count": target_opportunities,
            "facts_only_stage_proposition_emitted_count": 0,
            "counterfactual_single_code_retained_count": single_retained,
            "counterfactual_single_code_parent_context_elided_on_nested_count": (
                single_parent_elided
            ),
            "layered_target_proposition_retained_count": layered_retained,
            "layered_nested_rows_with_explicit_parent_context_count": (
                layered_nested_parent_explicit
            ),
            "layered_nested_rows_with_parent_context_elided_count": nested_count
            - layered_nested_parent_explicit,
            "development_shadow_semantic_mismatch_count": layered_mismatch,
            "layered_primary_stage_non_null_count": primary_non_null,
        },
        "interpretation_result": (
            "PASS_LAYERED_REPRESENTATION_PRESERVES_EXPLICIT_PARENT_AND_NESTED_SEMANTICS"
            if layered_mismatch == 0
            and primary_non_null == 0
            and layered_retained == target_opportunities
            and layered_nested_parent_explicit == nested_count
            else "FAIL_LAYERED_REPRESENTATION_CONTRACT"
        ),
        "predictive_utility_claim": False,
        "economic_utility_claim": False,
        "out_of_sample_claim": False,
        "claim_ceiling": request["knowledge_boundary"]["claim_ceiling"],
    }
    runtime = {
        "schema_version": "quanthus.stage-proposition-bundle-real-data-replay-runtime.v1",
        "source_artifact_reads": 2,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "future_outcome_reads": 0,
        "new_reference_opinions": 0,
        "source_record_view": "retrospective",
    }
    payloads = {
        "replay.json": canonical(replay),
        "summary.json": canonical(summary),
        "runtime.json": canonical(runtime),
    }
    return payloads, summary


def write_real_data_replay(output: Path, request: dict[str, Any], *, code_revision: str) -> None:
    payloads, summary = build_real_data_replay(request)
    if set(payloads) != FILES:
        raise ValueError("P029 replay payload inventory differs")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "code_revision": code_revision,
        "request_digest": digest(request),
        "research_only": True,
        "execution_authorized": False,
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "result_digest": digest(summary),
        "files": {
            name: "sha256:" + hashlib.sha256(value).hexdigest()
            for name, value in payloads.items()
        },
    }
    all_payloads = {**payloads, "manifest.json": canonical(manifest)}
    all_payloads["READY"] = canonical(
        {
            "schema_version": "quanthus.stage-proposition-bundle-real-data-replay-ready.v1",
            "manifest_sha256": "sha256:"
            + hashlib.sha256(all_payloads["manifest.json"]).hexdigest(),
        }
    )
    for name, data in all_payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def verify_stage_proposition_bundle_replay_artifact(
    root: Path, *, code_revision: str, request_digest: str
) -> dict[str, Any]:
    root = root.resolve()
    expected = FILES | {"manifest.json", "READY"}
    if not root.is_dir() or {path.name for path in root.iterdir()} != expected:
        raise ValueError("P029 replay artifact file set differs")
    if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
        raise ValueError("P029 replay artifact contains indirect members")
    manifest = _load(root / "manifest.json")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("code_revision") != code_revision
        or manifest.get("request_digest") != request_digest
        or manifest.get("research_only") is not True
        or manifest.get("execution_authorized") is not False
        or manifest.get("knowledge_view") != "retrospective"
        or manifest.get("strict_pit_claim") is not False
        or set(manifest.get("files") or {}) != FILES
    ):
        raise ValueError("P029 replay manifest differs")
    for name in FILES:
        if manifest["files"][name] != "sha256:" + hashlib.sha256(
            (root / name).read_bytes()
        ).hexdigest():
            raise ValueError("P029 replay payload digest differs")
    manifest_sha = "sha256:" + hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if _load(root / "READY") != {
        "schema_version": "quanthus.stage-proposition-bundle-real-data-replay-ready.v1",
        "manifest_sha256": manifest_sha,
    }:
        raise ValueError("P029 replay READY binding differs")

    project_root = find_project_root()
    request = load_replay_request(project_root / REQUEST_PATH)
    if digest(request) != request_digest:
        raise ValueError("P029 replay request digest differs")
    rebuilt, expected_summary = build_real_data_replay(request)
    for name in FILES:
        if rebuilt[name] != (root / name).read_bytes():
            raise ValueError("P029 replay deep reconstruction differs")
    summary = _load(root / "summary.json")
    if summary != expected_summary or digest(summary) != manifest["result_digest"]:
        raise ValueError("P029 replay summary differs")
    replay_rows = _load(root / "replay.json")
    if not isinstance(replay_rows, list) or len(replay_rows) != 10:
        raise ValueError("P029 replay rows differ")

    def compact_review_example(row: dict[str, Any]) -> dict[str, Any]:
        facts_only = row["facts_only"]
        shadow = row["development_shadow"]
        visible_codes = [
            item["historical_stage_code"]
            for item in (
                *shadow["context_assertions"],
                *shadow["condition_assertions"],
            )
        ]
        return {
            "observation_identity": row["observation_identity"],
            "facts_only": {
                "phase": facts_only["phase"],
                "unreclaimed_age_sessions": facts_only["unreclaimed_age_sessions"],
                "support_distance_value": facts_only["support_distance_value"],
                "post_break_rebound_value": facts_only["post_break_rebound_value"],
            },
            "counterfactual_single_code": row["counterfactual_single_code"]["selected_code"],
            "layered_visible_codes": visible_codes,
            "layered_primary_stage": shadow["primary_stage"],
        }

    context_example = next(
        row
        for row in replay_rows
        if row["counterfactual_single_code"]["selected_code"] == 10
    )
    nested_example = next(
        row
        for row in replay_rows
        if row["counterfactual_single_code"]["selected_code"] == 11
    )
    review_examples = [
        compact_review_example(context_example),
        compact_review_example(nested_example),
    ]

    metrics = summary["interpretation_metrics"]
    if (
        summary["observation_count"] != 10
        or summary["nested_10_11_count"] != 6
        or summary["context_only_count"] != 4
        or metrics["explicit_target_proposition_opportunity_count"] != 16
        or metrics["counterfactual_single_code_retained_count"] != 10
        or metrics["counterfactual_single_code_parent_context_elided_on_nested_count"] != 6
        or metrics["layered_target_proposition_retained_count"] != 16
        or metrics["layered_nested_rows_with_explicit_parent_context_count"] != 6
        or metrics["layered_nested_rows_with_parent_context_elided_count"] != 0
        or metrics["development_shadow_semantic_mismatch_count"] != 0
        or metrics["layered_primary_stage_non_null_count"] != 0
        or summary["market_data_reads"] != 0
        or summary["dwd_provider_opens"] != 0
        or summary["strict_point_in_time_claim"] is not False
    ):
        raise ValueError("P029 replay acceptance metrics differ")
    return {
        "artifact_ref": str(root),
        "manifest_sha256": manifest_sha,
        "result_digest": manifest["result_digest"],
        "source_support_facts_digest": summary["source_support_facts_digest"],
        "formal_projection_digest": summary["formal_projection_digest"],
        "row_count": summary["observation_count"],
        "p2_probe_count": 0,
        "p2_fill_count": 0,
        "nested_10_11_count": summary["nested_10_11_count"],
        "context_only_count": summary["context_only_count"],
        "single_code_parent_elision_count": metrics[
            "counterfactual_single_code_parent_context_elided_on_nested_count"
        ],
        "layered_parent_elision_count": metrics[
            "layered_nested_rows_with_parent_context_elided_count"
        ],
        "layered_retained_count": metrics["layered_target_proposition_retained_count"],
        "knowledge_view": "retrospective",
        "strict_pit_claim": False,
        "market_data_reads": 0,
        "dwd_provider_opens": 0,
        "interpretation_result": summary["interpretation_result"],
        "review_examples": review_examples,
        "execution_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    if Path(args.request).as_posix() != REQUEST_PATH:
        raise ValueError("P029 replay requires fixed governed request")
    if len(args.code_revision) != 40 or any(
        char not in "0123456789abcdef" for char in args.code_revision
    ):
        raise ValueError("P029 replay requires exact lowercase code revision")
    project_root = find_project_root()
    request = load_replay_request(project_root / REQUEST_PATH)
    output = Path(args.output)
    write_real_data_replay(output, request, code_revision=args.code_revision)
    verified = verify_stage_proposition_bundle_replay_artifact(
        output, code_revision=args.code_revision, request_digest=digest(request)
    )
    print(json.dumps(verified, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
