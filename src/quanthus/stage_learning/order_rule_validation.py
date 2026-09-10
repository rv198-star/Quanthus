"""Fixed Stage-007 selection and scoring over supplied, retained evidence.

No acquisition, SSH, Worker submission, opinion generation, or parameter search lives here.
Authenticated source membership and execution activation remain the runtime adapter's duties.
A validated caller-supplied identity map is not itself proof of external source authenticity.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning import order_reference_protocol as reference
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST, evaluate_case, load_rule
from quanthus.structure_v2_inputs import digest, exact_sha

PLAN_PATH = "config/stage-order-rule-screen-delivery.v1.json"
VALIDATION_DIGEST = "sha256:1512c0f0fdc46a7c0bcd18e4a7f3c46bae83a4d5e08a885dcb42632481bc9989"
OLD_MANIFEST = "sha256:67a1159eba611ceb8af1f43c246f49acc101786e223730af6b61a9b0a8c237ac"
OLD_REVISION = "1878c625ad04b23793507d92bf0e3008052a5a85"
MAX_SCAN_ROWS = 512 * 366
REFERENCES = ("STAGE_0", "STAGE_2", "NEITHER", "AMBIGUOUS", "INPUT_UNAVAILABLE",
              "RULE_CONFLICT")
PREDICTIONS = ("STAGE_0", "STAGE_2", "NO_ASSERTION", "INPUT_UNAVAILABLE", "RULE_CONFLICT")


class RuleValidationError(ValueError):
    """Evidence, identity, sampling, or a frozen validation boundary is inconsistent."""


def load_validation(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    load_rule(root)
    value = json.loads((root / PLAN_PATH).read_text(encoding="utf-8"))["validation"]
    if digest(value) != VALIDATION_DIGEST:
        raise RuleValidationError("validation_drift_requires_new_version")
    return value


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200


def _json(raw: bytes) -> Any:
    if not isinstance(raw, bytes) or len(raw) > reference.MAX_BYTES:
        raise RuleValidationError("bounded_json_required")

    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise RuleValidationError("duplicate_json_field")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise RuleValidationError("nonfinite_json:" + value)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError) as exc:
        raise RuleValidationError("invalid_json") from exc


def verify_old_cohort(
    manifest_bytes: bytes, summary_bytes: bytes, reconstructed_codes: Sequence[str],
) -> tuple[str, ...]:
    """Bind reconstructed 512 codes to the *actual* old, pinned summary, not just its count."""
    if not isinstance(manifest_bytes, bytes) or len(manifest_bytes) > reference.MAX_BYTES:
        raise RuleValidationError("bounded_manifest_required")
    if _sha(manifest_bytes) != OLD_MANIFEST:
        raise RuleValidationError("old_manifest_not_the_accepted_run")
    manifest, summary = _json(manifest_bytes), _json(summary_bytes)
    if not isinstance(manifest, dict) or not isinstance(summary, dict):
        raise RuleValidationError("old_artifact_object_required")
    if (manifest.get("code_revision") != OLD_REVISION
            or summary.get("code_revision") != OLD_REVISION
            or manifest.get("files", {}).get("summary.json") != _sha(summary_bytes)):
        raise RuleValidationError("old_summary_binding_mismatch")
    if (len(reconstructed_codes) != 512
            or any(not isinstance(c, str) or len(c) != 6 or not c.isascii()
                   or not c.isdigit() for c in reconstructed_codes)):
        raise RuleValidationError("old_cohort_reconstruction_mismatch")
    codes = tuple(sorted(reconstructed_codes))
    if (len(set(codes)) != 512 or summary.get("cohort_size") != 512
            or digest(codes) != summary.get("cohort_digest")):
        raise RuleValidationError("old_cohort_reconstruction_mismatch")
    return codes


def isolated_pool(
    old_codes: Sequence[str], identity_members: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """Apply exclusions to an already authenticated 2019-2021 identity-map projection.

This checks set algebra and completeness for required codes, not that the caller actually
obtained all mapping rows from a trusted source. Such external proof must be retained upstream.
Historical code reuse excludes every mapped economic identity; aliases cannot evade exclusion.
"""
    config = load_validation()
    if (len(old_codes) != 512 or not all(_text(code) for code in old_codes)
            or len(set(old_codes)) != 512):
        raise RuleValidationError("complete_old_cohort_required")
    if not identity_members or len(identity_members) > 100000:
        raise RuleValidationError("identity_map_budget")
    by_code: dict[str, set[str]] = {}
    by_identity: dict[str, set[str]] = {}
    members = set()
    for item in identity_members:
        if (set(item) != {"security_code", "security_id"}
                or not all(_text(v) for v in item.values())):
            raise RuleValidationError("invalid_identity_member")
        code, identity = item["security_code"], item["security_id"]
        if (code, identity) in members:
            raise RuleValidationError("duplicate_identity_member")
        members.add((code, identity))
        by_code.setdefault(code, set()).add(identity)
        by_identity.setdefault(identity, set()).add(code)
    required = set(old_codes) | set(
        config["excluded_stage006_codes_to_resolve_to_economic_identities"]
    )
    if not required <= set(by_code):
        raise RuleValidationError("unresolved_excluded_economic_identity")
    excluded = set().union(*(by_code[code] for code in required))
    # Multiple historical identities behind a proposed new code require explicit resolution.
    eligible = {identity: sorted(codes)[0] for identity, codes in by_identity.items()
                if identity not in excluded and all(len(by_code[c]) == 1 for c in codes)}
    ranked = sorted(eligible, key=lambda identity: (_rank("pool", identity), identity))[:512]
    return {
        "pool": {identity: eligible[identity] for identity in ranked},
        "excluded_economic_ids": sorted(excluded),
        "identity_members_digest": digest(sorted(members)),
        "old_codes_digest": digest(tuple(sorted(old_codes))),
        "source_membership_verified": False,
        "runtime_activation_authorized": False,
    }


def _rank(purpose: str, identity: str, day: date | None = None, bucket: str = "") -> str:
    return digest({"seed": "stage007-frozen-screen-20260911-v1", "purpose": purpose,
                   "security_id": identity, "market_date": day, "bucket": bucket})


@dataclass(frozen=True)
class SamplingObservation:
    """One supplied formal-Fact view; unavailable attempts remain coverage candidates."""
    security_id: str
    security_code: str
    market_date: date
    available: bool
    efficiency: Decimal | None
    width: Decimal | None
    width_change: Decimal | None
    center_shift: Decimal | None

    def check(self) -> None:
        if (not _text(self.security_id) or not _text(self.security_code)
                or type(self.market_date) is not date or self.market_date.year != 2021
                or type(self.available) is not bool):
            raise RuleValidationError("observation_identity_date_or_availability")
        values = (self.efficiency, self.width, self.width_change, self.center_shift)
        if any(v is not None and (not isinstance(v, Decimal) or not v.is_finite())
               for v in values):
            raise RuleValidationError("invalid_sampling_number")
        if self.available:
            if any(v is None for v in values):
                raise RuleValidationError("available_sampling_value_missing")
            if (self.efficiency.copy_abs() > 1 or not 0 <= self.width < 2
                    or self.width_change < -1 or self.center_shift <= -1):
                raise RuleValidationError("sampling_fact_range")

    @property
    def quarter(self) -> int:
        return (self.market_date.month - 1) // 3 + 1

    @classmethod
    def from_case(cls, case: dict) -> SamplingObservation:
        try:
            values = [case["primary"][key]["value"] for key in (
                "directional_efficiency", "range_width", "width_change", "center_shift",
            )]
            result = cls(case["security_id"], case["security_code"],
                         date.fromisoformat(case["market_date"]), case["primary_available"],
                         *(None if value is None else Decimal(value) for value in values))
            result.check()
            return result
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise RuleValidationError("invalid_case_for_selection") from exc


def _bucket(obs: SamplingObservation, bands: dict) -> str | None:
    if not obs.available or obs.efficiency.copy_abs() > Decimal(bands["efficiency_abs_max"]):
        return None
    if (obs.width <= Decimal(bands["order_width_max"])
            and obs.width_change <= Decimal(bands["order_width_change_max"])
            and obs.center_shift.copy_abs() <= Decimal(bands["order_center_abs_max"])):
        return "order_like"
    if (obs.width_change >= Decimal(bands["instability_width_change_min"])
            or obs.center_shift.copy_abs() >= Decimal(bands["instability_center_abs_min"])):
        return "instability_like"
    return "boundary"


def select_cases(
    pool: Mapping[str, str], observations: Iterable[SamplingObservation],
) -> dict[str, Any]:
    """One bounded pass, stable hashes, fixed coverage slots, and no quota backfill."""
    config = load_validation()
    if (not pool or len(pool) > 512 or len(set(pool.values())) != len(pool)
            or not all(_text(k) and _text(v) for k, v in pool.items())):
        raise RuleValidationError("pool_identity_budget_or_alias")
    identities = sorted(pool, key=lambda identity: (_rank("coverage_identity", identity), identity))
    coverage_slots = {identity: i // 16 + 1 for i, identity in enumerate(identities[:64])}
    best: dict[tuple[str, str], tuple[str, SamplingObservation]] = {}
    seen = set()
    for obs in observations:
        obs.check()
        if pool.get(obs.security_id) != obs.security_code:
            raise RuleValidationError("observation_outside_frozen_pool")
        key = (obs.security_id, obs.market_date)
        if key in seen:
            raise RuleValidationError("duplicate_identity_session")
        seen.add(key)
        if len(seen) > MAX_SCAN_ROWS:
            raise RuleValidationError("scan_row_budget")
        if obs.security_id in coverage_slots:
            if coverage_slots[obs.security_id] != obs.quarter:
                continue
            bucket = "coverage"
        else:
            bucket = _bucket(obs, config["sampling_bands"])
            if bucket is None:
                continue
        slot = (obs.security_id, bucket)
        order = _rank("date", obs.security_id, obs.market_date, bucket)
        if slot not in best or (order, obs.market_date) < (
            best[slot][0], best[slot][1].market_date,
        ):
            best[slot] = (order, obs)
    selected = []

    def add(obs: SamplingObservation, panel: str, bucket: str) -> None:
        selected.append({"security_id": obs.security_id, "security_code": obs.security_code,
                         "market_date": obs.market_date.isoformat(), "panel": panel,
                         "bucket": bucket, "quarter": obs.quarter,
                         "observation_digest": digest(obs)})

    for identity in coverage_slots:
        if (identity, "coverage") in best:
            add(best[(identity, "coverage")][1], "coverage", "coverage")
    used = set(coverage_slots)  # Missing coverage slots cannot re-enter as challenge examples.
    challenge = config["panels"]["challenge"]
    for bucket, target in zip(challenge["bucket_order"], challenge["bucket_targets"], strict=True):
        eligible = [identity for identity, observed_bucket in best
                    if observed_bucket == bucket and identity not in used]
        eligible.sort(key=lambda identity: (_rank("challenge_identity", identity, bucket=bucket),
                                            identity))
        for identity in eligible[:target]:
            add(best[(identity, bucket)][1], "challenge", bucket)
            used.add(identity)
    selected.sort(key=lambda item: (item["panel"], item["bucket"], item["security_id"]))
    counts = Counter(item["bucket"] for item in selected)
    payload = {
        "schema_version": "quanthus.stage-order-rule-selection.v1",
        "validation_digest": VALIDATION_DIGEST, "pool_digest": digest(dict(pool)),
        "pool_size": len(pool), "scanned_attempts": len(seen), "selected": selected,
        "coverage_slots": coverage_slots,
        "shortfalls": {"coverage": 64 - counts["coverage"], **{
            bucket: target - counts[bucket] for bucket, target in zip(
                challenge["bucket_order"], challenge["bucket_targets"], strict=True,
            )}},
        "source_isolation_verified": False, "runtime_activation_authorized": False,
    }
    return {**payload, "selection_digest": digest(payload)}


def _ratio(numerator: int, denominator: int) -> str | None:
    with localcontext(Context(prec=28)):
        return None if not denominator else str(Decimal(numerator) / Decimal(denominator))


def metrics(pairs: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """Complete descriptive denominators; a reference UNKNOWN is not a fabricated negative."""
    if len(pairs) > 160:
        raise RuleValidationError("score_case_budget")
    matrix = {ref: dict.fromkeys(PREDICTIONS, 0) for ref in REFERENCES}
    for item in pairs:
        ref, pred = item["reference"], item["prediction"]
        if ref not in REFERENCES or pred not in PREDICTIONS:
            raise RuleValidationError("unknown_reference_or_prediction")
        matrix[ref][pred] += 1
    classes = {}
    for label in ("STAGE_0", "STAGE_2"):
        negatives = {"STAGE_0", "STAGE_2", "NEITHER"} - {label}
        tp = matrix[label][label]
        fp = sum(matrix[ref][label] for ref in negatives)
        positive = sum(matrix[label].values())
        negative = sum(sum(matrix[ref].values()) for ref in negatives)
        classes[label] = {"true_positive": tp, "false_positive": fp,
                          "false_negative": positive - tp,
                          "definite_positive_references": positive,
                          "definite_negative_references": negative,
                          "definite_predictions": tp + fp,
                          "precision": _ratio(tp, tp + fp),
                          "positive_reference_coverage": _ratio(tp, positive),
                          "definite_negative_false_positive_rate": _ratio(fp, negative)}
    ambiguous = sum(matrix["AMBIGUOUS"].values())
    asserted = sum(matrix["AMBIGUOUS"][label] for label in ("STAGE_0", "STAGE_2"))
    return {
        "case_count": len(pairs), "matrix": matrix, "classes": classes,
        "complete_available_cases": sum(
            item["reference"] != "INPUT_UNAVAILABLE" and item["prediction"] != "INPUT_UNAVAILABLE"
            for item in pairs
        ),
        "ambiguous_references": ambiguous, "ambiguous_assertions": asserted,
        "ambiguous_assertion_rate": _ratio(asserted, ambiguous),
        "conflict_count": sum("RULE_CONFLICT" in (p["reference"], p["prediction"]) for p in pairs),
        "availability_mismatch_count": sum(
            (p["reference"] == "INPUT_UNAVAILABLE") != (p["prediction"] == "INPUT_UNAVAILABLE")
            for p in pairs
        ),
    }


def assess_gates(benchmark: dict, coverage: dict) -> dict[str, Any]:
    """A component-level gate calculation, never evidence of authenticated input or independence."""
    cfg = load_validation()
    if benchmark["conflict_count"] or benchmark["availability_mismatch_count"]:
        return {"disposition": "BLOCKED_EVIDENCE", "reasons": ["conflict_or_availability_mismatch"]}
    reasons = []
    if benchmark["complete_available_cases"] < cfg["minimum_complete_cases"]:
        reasons.append("complete_reference_count_below_gate")
    if benchmark["ambiguous_references"] < cfg["minimum_ambiguous_references"]:
        reasons.append("ambiguous_reference_count_below_gate")
    for label, value in benchmark["classes"].items():
        for field, gate in (
            ("definite_positive_references", "minimum_definite_positives_per_class"),
            ("definite_negative_references", "minimum_definite_negatives_per_class"),
            ("definite_predictions", "minimum_definite_predictions_per_class"),
        ):
            if value[field] < cfg[gate]:
                reasons.append(label + ":" + field + "_below_gate")
    if reasons:
        return {"disposition": "INCONCLUSIVE_NO_SUPPLEMENT", "reasons": reasons}
    for label, value in benchmark["classes"].items():
        for field, gate, minimum in (
            ("precision", "minimum_benchmark_precision", True),
            ("positive_reference_coverage", "minimum_positive_reference_coverage", True),
            ("definite_negative_false_positive_rate",
             "maximum_definite_negative_false_positive_rate", False),
        ):
            observed, threshold = Decimal(value[field]), Decimal(cfg[gate])
            if (minimum and observed < threshold) or (not minimum and observed > threshold):
                reasons.append(label + ":" + field + "_fails")
        cover = coverage["classes"][label]
        veto_floor = Decimal(cfg["coverage_panel_veto_precision_below"])
        if (cover["definite_predictions"] >= cfg["coverage_panel_veto_min_predictions"]
                and Decimal(cover["precision"]) < veto_floor):
            reasons.append(label + ":coverage_panel_veto")
    ambiguity_ceiling = Decimal(cfg["maximum_ambiguous_assertion_rate"])
    if Decimal(benchmark["ambiguous_assertion_rate"]) > ambiguity_ceiling:
        reasons.append("ambiguous_assertion_rate_fails")
    return {"disposition": "REVISE_TARGET_OR_PARK" if reasons else "PROMISING_EXPLORATORY_ONLY",
            "reasons": reasons or ["independent_semantic_evidence_not_established"]}


def score_sealed_bundle(
    bundle_root: Path, selection: dict, *, expected_manifest: str, expected_selection: str,
) -> dict[str, Any]:
    """Read/verify an existing opinion bundle before evaluating the one frozen candidate.

No reference is generated or modified. The caller must separately authenticate source isolation,
selection sealing order and reviewer visibility before this component can support a run disposition.
"""
    if not exact_sha(expected_manifest) or not exact_sha(expected_selection):
        raise RuleValidationError("external_expected_bindings_required")
    selection_body = {k: v for k, v in selection.items() if k != "selection_digest"}
    if (selection.get("selection_digest") != expected_selection
            or digest(selection_body) != expected_selection
            or selection.get("validation_digest") != VALIDATION_DIGEST):
        raise RuleValidationError("selection_binding_mismatch")
    selected = selection.get("selected")
    if not isinstance(selected, list) or not 0 < len(selected) <= 160:
        raise RuleValidationError("selected_case_budget")
    if len({item["security_id"] for item in selected}) != len(selected):
        raise RuleValidationError("duplicate_selected_identity")
    counts = Counter(item["bucket"] for item in selected)
    caps = {"coverage": 64, "order_like": 48, "instability_like": 32, "boundary": 16}
    if any(bucket not in caps or count > caps[bucket] for bucket, count in counts.items()):
        raise RuleValidationError("selection_bucket_budget")
    slots = selection.get("coverage_slots", {})
    for item in selected:
        covered = item["panel"] == "coverage"
        if (covered != (item["bucket"] == "coverage")
                or covered != (item["security_id"] in slots)
                or (covered and slots[item["security_id"]] != item["quarter"])):
            raise RuleValidationError("selection_panel_or_slot_mismatch")
    verified = reference.verify_bundle(bundle_root)
    if verified["manifest_sha256"] != expected_manifest:
        raise RuleValidationError("wrong_sealed_reference_bundle")
    manifest_raw = (bundle_root / "manifest.json").read_bytes()
    if _sha(manifest_raw) != expected_manifest:
        raise RuleValidationError("bundle_changed_during_score")
    manifest = _json(manifest_raw)
    payloads = {}
    for name in ("evidence.json", "reviews.json", "resolved.json"):
        with (bundle_root / name).open("rb") as stream:
            raw = stream.read(reference.MAX_BYTES + 1)
        if len(raw) > reference.MAX_BYTES or _sha(raw) != manifest["files"][name]:
            raise RuleValidationError("bundle_changed_during_score")
        payloads[name] = _json(raw)
    evidence = payloads["evidence.json"]
    cases = {(case["security_id"], case["market_date"]): case for case in evidence["cases"]}
    chosen = {(item["security_id"], item["market_date"]): item for item in selected}
    if len(cases) != len(evidence["cases"]) or set(cases) != set(chosen):
        raise RuleValidationError("sealed_case_denominator_differs")
    resolved = {item["case_id"]: item for item in payloads["resolved.json"]}
    opinions_by_case: dict[str, list[dict]] = {}
    for raw_opinion in payloads["reviews.json"]:
        opinions_by_case.setdefault(raw_opinion["case_id"], []).append(raw_opinion)
    rows = []
    for key, case in sorted(cases.items()):
        item = chosen[key]
        obs = SamplingObservation.from_case(case)
        if (item["security_code"] != case["security_code"]
                or item["observation_digest"] != digest(obs) or item["quarter"] != obs.quarter
                or item["panel"] not in {"coverage", "challenge"}):
            raise RuleValidationError("selected_observation_differs")
        if (item["panel"] == "challenge"
                and item["bucket"] != _bucket(obs, load_validation()["sampling_bands"])):
            raise RuleValidationError("selected_challenge_bucket_differs")
        left, right = opinions_by_case[case["case_id"]]
        prediction = evaluate_case(case)
        rows.append({**item, "case_id": case["case_id"],
                     "reference": resolved[case["case_id"]]["primary_reference"],
                     "primary_agreement": left["primary"] == right["primary"],
                     "joint_primary_unknown": left["primary"] == right["primary"]
                     and "UNKNOWN" in left["primary"].values(),
                     "recent_agreement": resolved[case["case_id"]]["recent_agreement"],
                     "prediction": prediction["decision"], "candidate_result": prediction})
    panels = {panel: metrics([row for row in rows if row["panel"] == panel])
              for panel in ("coverage", "challenge")}
    benchmark = metrics(rows)
    return {
        "schema_version": "quanthus.stage-order-rule-score-component.v1",
        "status": "SCORING_COMPONENT_ONLY", "rule_digest": RULE_DIGEST,
        "validation_digest": VALIDATION_DIGEST, "selection_digest": expected_selection,
        "reference_manifest_sha256": expected_manifest,
        "opinion_digest": manifest["files"]["reviews.json"],
        "gate_calculation": assess_gates(benchmark, panels["coverage"]),
        "benchmark": benchmark, "panels": panels,
        "primary_disagreement_count": sum(not row["primary_agreement"] for row in rows),
        "joint_primary_unknown_count": sum(row["joint_primary_unknown"] for row in rows),
        "recent_disagreement_count": sum(not row["recent_agreement"] for row in rows),
        "quarters": {str(q): metrics([row for row in rows if row["quarter"] == q])
                     for q in range(1, 5)},
        "panel_quarters": {panel: {str(q): metrics([
            row for row in rows if row["panel"] == panel and row["quarter"] == q
        ]) for q in range(1, 5)} for panel in panels},
        "cases": rows, "source_kind": evidence["source_kind"],
        "review_source_kinds": verified["review_source_kinds"],
        "distinct_origin_count": verified["distinct_origin_count"],
        "human_expert_validated": False, "independent_validation": False,
        "source_isolation_verified": False, "full_context_blinding_claimed": False,
        "market_representativeness": False, "official_labels_authorized": False,
        "runtime_activation_authorized": False, "primary_stage": None,
    }
