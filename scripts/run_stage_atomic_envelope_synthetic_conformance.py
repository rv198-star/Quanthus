from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.atomic_primary_reference import (
    resolve_atomic_primary_roles,
    summarize_atomic_reference_batch,
)
from quanthus.stage_learning.order_reference_protocol_v2 import (
    PRIMARY_COMMIT_SCHEMA,
    PRIMARY_OPINION_SCHEMA,
    PRIMARY_PROPOSITIONS,
    PROTOCOL_DIGEST,
)
from quanthus.stage_learning.range_envelope_prototype import assess_close_envelope
from quanthus.structure_v2_inputs import digest

CONTRACT_PATH = "config/stage-learning/stage-atomic-envelope-synthetic-contract.v1.json"


def _contract(root: Path) -> dict[str, Any]:
    return json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))


def _synthetic_commit(
    *, case_name: str, role: str, values: list[str]
) -> dict[str, Any]:
    primary = dict(zip(PRIMARY_PROPOSITIONS, values, strict=True))
    opinion = {
        "schema_version": PRIMARY_OPINION_SCHEMA,
        "case_id": digest({"synthetic_case": case_name}),
        "protocol_digest": PROTOCOL_DIGEST,
        "packet_digest": digest({"synthetic_packet": case_name}),
        "reviewer_id": f"synthetic-{role}",
        "role": role,
        "provenance": {
            "kind": "deterministic_policy",
            "origin_id": "p012-e3-synthetic-conformance",
            "session_id": f"{case_name}-{role}",
        },
        "primary": primary,
        "support_refs": (
            [digest({"synthetic_support": case_name, "role": role})]
            if "TRUE" in primary.values()
            else []
        ),
        "contradiction_refs": [],
        "reason": "Frozen synthetic atomic proposition case for P3-STAGE-012-E3.",
        "unknowns": (
            ["synthetic_input_unavailable"] if "UNKNOWN" in primary.values() else []
        ),
    }
    return {
        "schema_version": PRIMARY_COMMIT_SCHEMA,
        "opinion": opinion,
        "primary_opinion_digest": digest(opinion),
    }


def run(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    contract = _contract(root)
    atomic_results = []
    checks: dict[str, bool] = {}
    for item in contract["atomic_cases"]:
        commits = [
            _synthetic_commit(
                case_name=item["id"], role="short_term", values=item["short_term"]
            ),
            _synthetic_commit(case_name=item["id"], role="fund", values=item["fund"]),
        ]
        resolved = resolve_atomic_primary_roles(
            commits, primary_available=item["primary_available"]
        )
        projection_ok = resolved["projected_primary_reference"] == item["expected_projection"]
        mixed_ok = resolved["mixed_directional_instability_roles"] == item["expected_mixed_roles"]
        checks[f"atomic:{item['id']}:projection"] = projection_ok
        checks[f"atomic:{item['id']}:mixed_roles"] = mixed_ok
        atomic_results.append(
            {
                "id": item["id"],
                "projected_primary_reference": resolved["projected_primary_reference"],
                "mixed_directional_instability_roles": resolved[
                    "mixed_directional_instability_roles"
                ],
                "role_atomic_primary": resolved["role_atomic_primary"],
            }
        )
    atomic_summary = summarize_atomic_reference_batch(
        [
            resolve_atomic_primary_roles(
                [
                    _synthetic_commit(
                        case_name=item["id"],
                        role="short_term",
                        values=item["short_term"],
                    ),
                    _synthetic_commit(
                        case_name=item["id"], role="fund", values=item["fund"]
                    ),
                ],
                primary_available=item["primary_available"],
            )
            for item in contract["atomic_cases"]
        ]
    )

    envelope_results = []
    for item in contract["envelope_cases"]:
        observed = assess_close_envelope(item["closes"]).to_dict()
        descriptors = observed["descriptors"]
        expected = item["expected"]
        observed_subset = {
            "status": observed["status"],
            "escape_count": descriptors["escape_count"],
            "resolved_escape_count": descriptors["resolved_escape_count"],
            "unresolved_escape_count": descriptors["unresolved_escape_count"],
            "resolved_escape_side_sequence": descriptors["resolved_escape_side_sequence"],
            "resolved_escape_side_alternation_count": descriptors[
                "resolved_escape_side_alternation_count"
            ],
            "resolved_escape_both_sides": descriptors["resolved_escape_both_sides"],
            "unresolved_escape_sides": descriptors["unresolved_escape_sides"],
        }
        checks[f"envelope:{item['id']}"] = observed_subset == expected
        envelope_results.append({"id": item["id"], **observed_subset})

    by_id = {item["id"]: item for item in envelope_results}
    checks["semantic:orderly_high_churn_not_failed_resolution"] = (
        by_id["orderly-high-churn-contraction"]["escape_count"] == 0
    )
    checks["semantic:stable_narrow_noise_not_failed_resolution"] = (
        by_id["stable-narrow-noise"]["escape_count"] == 0
    )
    checks["semantic:one_leg_repricing_persists_one_side"] = (
        by_id["one-leg-repricing-then-hold"]["unresolved_escape_sides"] == ["UPPER"]
        and by_id["one-leg-repricing-then-hold"]["resolved_escape_count"] == 0
    )
    checks["semantic:failed_resolution_alternates_both_sides"] = (
        by_id["compact-failed-resolution-disorder"]["resolved_escape_both_sides"] is True
        and by_id["compact-failed-resolution-disorder"][
            "resolved_escape_side_alternation_count"
        ]
        == 3
    )
    checks["semantic:unavailable_fails_closed"] = (
        by_id["unavailable-close"]["status"] == "UNAVAILABLE"
    )
    checks["authority:no_market_or_fact"] = (
        contract["authority"]["market_data"] is False
        and contract["authority"]["threshold_search"] is False
        and contract["authority"]["new_fact_registration"] is False
        and contract["authority"]["preserved_2021_evaluation_read"] is False
    )

    failed = sorted(key for key, value in checks.items() if not value)
    return {
        "schema_version": "quanthus.stage-atomic-envelope-synthetic-conformance-result.v1",
        "work_item": "P3-STAGE-012-E3",
        "state": "PASS_SYNTHETIC_CONFORMANCE" if not failed else "FAIL_SYNTHETIC_CONFORMANCE",
        "contract_path": CONTRACT_PATH,
        "contract_digest": digest(contract),
        "checks": checks,
        "failed_checks": failed,
        "atomic_results": atomic_results,
        "atomic_summary": atomic_summary,
        "envelope_results": envelope_results,
        "claim_ceiling": (
            "Synthetic-only conformance. No market separability, Fact qualification, "
            "threshold validity or Stage qualification is claimed."
        ),
        "authority": {
            "market_data_read": False,
            "preserved_2021_evaluation_read": False,
            "threshold_search": False,
            "candidate_generation": False,
            "new_fact_registration": False,
            "p008_relocation_change": False,
        },
    }


def main() -> int:
    print(json.dumps(run(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
