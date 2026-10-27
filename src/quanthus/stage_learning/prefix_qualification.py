"""Fixed-candidate development replication on a P10-qualified 2021 IPO prefix.

This successor cannot invoke the consumed arbitration reader or any acquisition.
Reference sufficiency is decided before constructing candidate predictions.
"""
from __future__ import annotations

import json
import os
import resource
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.p10_stage34_prefix import checked_member, sha_file
from quanthus.stage_learning.core import ActivityObservation, build_activity_observations
from quanthus.stage_learning.learning import (
    build_learning_observations,
    predict_stage,
    r0_parameters,
    score_predictions,
)
from quanthus.stage_learning.native_prefix import (
    NativeAction,
    NativePrefixScope,
    project_native_prefix,
)
from quanthus.stage_learning.qualification import (
    _hard_gate,
    _trial,
    select_qualification_contexts,
)
from quanthus.stage_learning.reference import (
    ReferenceContext,
    ReferenceKind,
    ResolvedReference,
    build_reviewer_packet,
)
from quanthus.stage_learning.review_ledger import resolve_complete_review
from quanthus.stage_learning.synthetic_panel import evaluate_panel, load_synthetic_panel
from quanthus.structure_v2 import StructureV2Result, build_structure_v2
from quanthus.structure_v2_inputs import Admission, InputPolicy, canonical, digest


def reference_sufficiency(
    contexts: Sequence[ReferenceContext], references: Sequence[ResolvedReference],
    audits: Sequence[Mapping[str, Any]], gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Count only definite truth, retaining coexistence and missingness separately."""
    ids = {c.context_id for c in contexts}
    if (len(ids) != len(contexts) or len(references) != len(contexts)
            or len({r.context_id for r in references}) != len(references)
            or {r.context_id for r in references} != ids):
        raise ValueError("PREFIX_REFERENCE_IDENTITY")
    kinds = Counter(r.reference_kind.value for r in references)
    definite = {str(stage): sum(r.reference_kind is ReferenceKind.DEFINITE
                               and r.supported_stage_codes == (stage,) for r in references)
                for stage in (3, 4)}
    reasons = []
    for stage, count in definite.items():
        if count < gates["minimum_definite_references_per_stage"]:
            reasons.append(f"stage_{stage}_reference_count_below_gate")
    if kinds["ambiguous_set"] < gates["minimum_ambiguous_references_for_overassertion_audit"]:
        reasons.append("ambiguous_reference_count_below_gate")
    rate = kinds["unjudgeable"] / len(references) if references else 1.0
    if rate > gates["maximum_unjudgeable_reference_rate"]:
        reasons.append("unjudgeable_reference_rate_above_gate")
    if len({c.episode_id for c in contexts}) != len(contexts):
        reasons.append("duplicate_episode")
    vetoes = sum(bool(a.get("veto")) for a in audits)
    if vetoes:
        reasons.append("quantitative_audit_veto")
    return {"passed": not reasons, "reason_codes": reasons,
            "reference_count": len(references), "reference_kind_counts": dict(kinds),
            "definite_stage_counts": definite, "unjudgeable_rate": rate,
            "quantitative_audit_veto_count": vetoes}


def score_if_sufficient(
    contexts: Sequence[ReferenceContext], references: Sequence[ResolvedReference],
    audits: Sequence[Mapping[str, Any]], structure: StructureV2Result,
    activity: Sequence[ActivityObservation], candidate: Mapping[str, Any],
    experiment: Mapping[str, Any], gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Assess recognition quality, never issue use-specific consumer authority.

    A passing result is eligible to accompany a separate consumer request. An
    admitted use still needs its own purpose, comparison and bound authority
    receipt; none is an input to this quality-only function.
    """
    sufficiency = reference_sufficiency(contexts, references, audits, gates)
    result: dict[str, Any] = {"reference_sufficiency": sufficiency,
                             "candidate_scored": False, "quality_passed": False,
                             "consumer_request_eligible": False,
                             "consumer_test_authorized": False,
                             "production_or_P6_admitted": False}
    if not sufficiency["passed"]:
        return {**result, "disposition": "INCONCLUSIVE_REFERENCE_SHORTFALL_NOT_SCORED"}
    observations = build_learning_observations(contexts, structure, activity)
    if {o.context_id for o in observations} != {c.context_id for c in contexts}:
        raise ValueError("PREFIX_LEARNING_OBSERVATION_IDENTITY")
    params = _trial(candidate)
    r0 = r0_parameters(experiment)
    scores = []
    for p in (params, r0):
        predictions = {o.context_id: predict_stage(o, p) for o in observations}
        scores.append(score_predictions(references, predictions, trial_id=p.trial_id).to_dict())
    gate = _hard_gate(scores[0], r0_score=scores[1], hard_gates=gates,
                      reference_count=len(references))
    return {**result, "candidate_scored": True, "candidate_score": scores[0],
            "r0_score": scores[1], "hard_gate": gate,
            "disposition": ("BOUNDED_DEVELOPMENT_REPLICATION_PASS" if gate["passed"]
                            else "REJECTED_RETAIN_FIXED_CANDIDATE_RESULT"),
            "quality_passed": gate["passed"],
            "consumer_request_eligible": gate["passed"]}


def run_prefix_qualification(
    *, source_root: Path, handoff_path: Path, handoff_sha256: str,
    repo: Path, request_path: Path, request_sha256: str,
    output: Path, budget_path: Path,
) -> dict[str, Any]:
    """One reference run, one conditional score; every read stays inside the handoff.

    The caller freezes a separate execution budget after source qualification.
    Ordinary code/config files are the only other inputs. Private review packets
    stay outside Git and safe handoff exports.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    started = time.monotonic()
    budget = json.loads(budget_path.read_text())
    if (output.exists() or output.resolve() != output or source_root.resolve() != source_root
            or source_root.is_relative_to(output) or output.is_relative_to(source_root)
            or repo.is_relative_to(output) or output.is_relative_to(repo)
            or not handoff_path.is_relative_to(source_root)
            or sha_file(handoff_path) != handoff_sha256
            or sha_file(request_path) != request_sha256
            or budget["source_handoff_sha256"] != handoff_sha256
            or budget["request_sha256"] != request_sha256
            or budget["output"] != str(output)
            or budget["new_network_requests"] != 0):
        raise ValueError("PREFIX_FROZEN_INPUT_OR_OUTPUT")
    request = json.loads(request_path.read_text())
    handoff = json.loads(handoff_path.read_text())
    if (request["purpose"] != "DEVELOPMENT_RETROSPECTIVE_FIXED_CANDIDATE_REPLICATION"
            or handoff["status"] != "QUALIFIED_RETROSPECTIVE_COORDINATE_ONLY"
            or handoff["request_id"] != request["request_id"]
            or handoff["selected_issuers"] != request["input_selection"]["issuers"]
            or handoff["selection_sha256"] != request["input_selection"]["sha256"]
            or handoff["business_year"] != 2021 or handoff["through"] != "2021-12-31"
            or handoff["historical_as_known"] is not False
            or request["reference_tier"] != "SYNTHETIC_EXPERT_CONSENSUS"):
        raise ValueError("PREFIX_PURPOSE_OR_SOURCE_QUALIFICATION")
    for m in budget["code_and_config_members"]:
        if sha_file(checked_member(repo, m["path"])) != m["sha256"]:
            raise ValueError("PREFIX_CODE_OR_CONFIG_CHANGED")

    def guard() -> None:
        if (time.monotonic()-started > budget["maximum_active_seconds"]
                or datetime.now(timezone.utc) > datetime.fromisoformat(budget["deadline"])
                or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024 > budget["rss_bytes"]
                or os.statvfs(output.parent).f_bavail * os.statvfs(output.parent).f_frsize
                < budget["minimum_free_bytes"]):
            raise ValueError("PREFIX_EXECUTION_BUDGET")
        if output.exists() and sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > (
            budget["maximum_output_bytes"]
        ):
            raise ValueError("PREFIX_OUTPUT_BUDGET")

    files = {f["path"]: f for f in handoff["files"]}
    if len(files) != len(handoff["files"]):
        raise ValueError("PREFIX_DUPLICATE_MEMBER")
    for relative, member in files.items():
        guard()
        p = checked_member(source_root, relative)
        if p.stat().st_size != member["size_bytes"] or sha_file(p) != member["sha256"]:
            raise ValueError("PREFIX_LOCAL_HANDOFF_MEMBER_CHANGED")
    selection = checked_member(source_root, handoff["selection_path"])
    if handoff["selection_path"] not in files or sha_file(selection) != handoff["selection_sha256"]:
        raise ValueError("PREFIX_SELECTION_IDENTITY")
    selected = json.loads(selection.read_text())["proposed"]
    if (len(selected) != handoff["selected_issuers"]
            or any(s["first_listing"] != s["proposed_start"]
                   or not "2021-01-01" <= s["first_listing"] <= s["proposed_through"]
                   <= "2021-12-31" for s in selected)):
        raise ValueError("PREFIX_SELECTION_WINDOW")
    tables = {}
    for m in handoff["native_materializations"]:
        if m["path"] not in files or m["sha256"] != files[m["path"]]["sha256"]:
            raise ValueError("PREFIX_TABLE_IDENTITY")
        p = pq.ParquetFile(checked_member(source_root, m["path"]))
        if p.metadata.num_rows != m["rows"]:
            raise ValueError("PREFIX_TABLE_ROWS")
        tables[m["table"]] = p.read().to_pylist()
    # No call to the old 2022-2024 run_qualification / qualification source reader.
    protocol = json.loads((repo / request["semantic_protocol"]).read_text())
    old = json.loads((repo / "config/stage-learning/qualification.v1.json").read_text())
    candidate = json.loads((repo / request["frozen_candidate"]).read_text())
    if digest(candidate) != protocol["frozen_candidate"]["digest"]:
        raise ValueError("PREFIX_FROZEN_CANDIDATE")
    experiment = json.loads((repo / old["experiment_config"]).read_text())
    harness = json.loads((repo / "config/stage-learning/harness.v1.json").read_text())
    panel = load_synthetic_panel(repo / old["synthetic_panel_config"])
    spec = digest({"request": request, "source": handoff_sha256,
                   "facts": harness["facts"], "first_group": experiment["first_group"],
                   "r0": harness["r0_engineering_rules"]})
    calendar_ref = digest(tables["dwd_trading_calendar"])
    admissions: list[Admission] = []
    for s in selected:
        guard()
        actions = [NativeAction(a["action_id"], date.fromisoformat(a["ex_date"]),
                                a["action_type"], a["source_revision"], a["evidence_digest"])
                   for a in handoff["canonical_action_date_boundaries"] if a["code"] == s["code"]]
        exchange = "SSE" if s["code"].startswith("6") else "SZSE"
        scope = NativePrefixScope(s["code"], s["security_id"], s["listing_id"], exchange,
                                  date.fromisoformat(s["first_listing"]),
                                  date.fromisoformat(s["proposed_through"]),
                                  "sha256:"+handoff_sha256,
                                  digest({"handoff": handoff_sha256, "code": s["code"]}),
                                  digest([a.identity() for a in actions]))
        admissions.extend(project_native_prefix(
            scope, InputPolicy(spec, (s["code"],), calendar_ref),
            listings=[r for r in tables["dwd_listing"] if r["listing_id"] == s["listing_id"]],
            calendar=[r for r in tables["dwd_trading_calendar"] if r["venue_id"] == s["venue_id"]],
            bars=[r for r in tables["dwd_security_bar"] if r["listing_id"] == s["listing_id"]],
            states=[r for r in tables["dwd_tradability_state"]
                    if r["listing_id"] == s["listing_id"]],
            gaps=[r for r in tables["dwd_observation_gap"] if r["listing_id"] == s["listing_id"]],
            exceptions=tables["dwd_quality_exception"], actions=actions,
        ))
    admissions.sort(key=lambda a: a.key)
    structure = build_structure_v2(tuple(admissions), spec_digest=spec,
                                   calendar_ref=calendar_ref, lookbacks=(20, 60))
    activity = build_activity_observations(tuple(admissions), window=20)
    guard()
    sampling = {**old["sampling"], "strata": protocol["sampling"]["target_caps"],
                "started_activity_buckets": protocol["sampling"]["started_activity_target_caps"],
                "early_held_per_hold_count": protocol["sampling"]["early_held_target_caps"]}
    contexts = select_qualification_contexts(structure.episodes, activity,
                                            report_start=date(2021, 1, 1),
                                            report_end=date(2021, 12, 31),
                                            sampling=sampling, target_caps=True)
    output.mkdir()

    def save(name: str, value: Any) -> None:
        guard()
        payload = canonical(value)+b"\n"
        if len(payload) > budget["maximum_output_bytes"]:
            raise ValueError("PREFIX_OUTPUT_BUDGET")
        with (output / name).open("xb") as stream:
            stream.write(payload)
        guard()

    counts = dict(Counter(c.sampling_stratum for c in contexts))
    save("sample-manifest.private.json", {"contexts": [c.to_dict() for c in contexts],
                                         "source_handoff_sha256": handoff_sha256,
                                         "sampling": sampling, "realized_counts": counts})
    references: tuple[ResolvedReference, ...] = ()
    evaluated: dict[str, Any] = {"audits": []}
    if contexts:
        packets = build_reviewer_packet(contexts, structure.episodes, activity, admissions,
                                         history_sessions=sampling["review_history_sessions"])
        save("review-packets.private.json", packets)
        evaluated = evaluate_panel(panel, packets,
                                   {c.context_id: c.evidence_digest for c in contexts})
        references = resolve_complete_review(contexts,
                                             [evaluated["primary_a"], evaluated["primary_b"]],
                                             evaluated["adjudicator"])
    save("resolved-references.private.json", [r.to_dict() for r in references])
    save("reference-reviews.private.json", evaluated)
    sufficiency = reference_sufficiency(contexts, references, evaluated["audits"],
                                        old["hard_gates"])
    save("reference-sufficiency.safe.json", sufficiency)
    guard()
    result = score_if_sufficient(contexts, references, evaluated["audits"], structure,
                                 activity, candidate, experiment, old["hard_gates"])
    summary = {
        "schema_version": "quanthus.stage34-prefix-qualification.v1",
        "UTC": datetime.now(timezone.utc).isoformat(), "request_id": request["request_id"],
        "purpose": request["purpose"], "source_handoff_sha256": handoff_sha256,
        "request_sha256": request_sha256, "candidate_digest": digest(candidate),
        "panel_digest": digest(panel), "sampling_digest": digest(sampling),
        "context_digest": digest([c.to_dict() for c in contexts]),
        "reference_digest": digest([r.to_dict() for r in references]),
        "selected_issuers": len(selected), "admission_attempts": len(admissions),
        "admission_reasons": dict(Counter(a.reason for a in admissions)),
        "episode_observations": len(structure.episodes), "context_count": len(contexts),
        "realized_stratum_counts": counts, "lookbacks": [20, 60],
        "reference_tier": "SYNTHETIC_EXPERT_CONSENSUS", "human_reference": False,
        "historical_as_known": False, "protected_2022_2026_read": False,
        "parameter_search": False, "production_or_P6_admitted": False,
        **result, "elapsed_seconds": time.monotonic()-started,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        "source_TMP_reused": True, "new_network_requests": 0,
    }
    save("assessment.safe.json", summary)
    return summary
