from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.structure_directional_path_envelope import assess_close_path_envelope
from quanthus.structure_directional_path_envelope_representation import (
    CONTRACT_DIGEST,
    RESULT_KIND,
    as_catalog_measurement,
    load_contract,
)
from quanthus.structure_semantics import load_semantic_catalog
from quanthus.structure_v2_inputs import digest
from quanthus.temporal_contracts import BusinessTime, ObservationRevision

ROOT = find_project_root()
AUTH = ROOT / "config/structure-directional-path-envelope-e7-authorization.v1.json"
AUTH_DIGEST = "sha256:2ac2219402151cca13c6ff805ba32fd633bf198c5f55d0629332b687203d8225"
E5_SYNTHETIC = ROOT / "config/structure-directional-path-envelope-synthetic-contract.v1.json"
E5_SYNTHETIC_DIGEST = "sha256:4dcef798bc12cabf4cc0f0e67c016bc93eb504f1f9cf1bbacb9f8d04d6c6e0ea"
CATALOG = ROOT / "config/structure-semantic-catalog.v1.json"
CATALOG_DIGEST = "sha256:86bf671c163c69002330102e15b120cd3883b7064d32d806c6f30680082e3a4d"
B6_PATH = ROOT / "config/structure-directional-path-geometry.v1.json"
B6_PATH_DIGEST = "sha256:d944a1c503b0d6bb71dfae247c94378361f1a4100174a6da93c944cc2cc1030c"


def _load(path: Path, expected_digest: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if digest(value) != expected_digest:
        raise RuntimeError(f"digest_drift:{path}")
    return value


def run() -> dict[str, object]:
    auth = _load(AUTH, AUTH_DIGEST)
    synthetic = _load(E5_SYNTHETIC, E5_SYNTHETIC_DIGEST)
    catalog_raw = _load(CATALOG, CATALOG_DIGEST)
    _load(B6_PATH, B6_PATH_DIGEST)
    representation = load_contract(ROOT)
    if digest(representation) != CONTRACT_DIGEST:
        raise RuntimeError("representation_contract_drift")

    catalog = load_semantic_catalog(CATALOG)
    concept_id = representation["concept"]["concept_id"]
    checks: dict[str, bool] = {}
    checks["catalog:family_owned"] = catalog.family_of(concept_id).boundary == "owned"
    try:
        catalog.resolve(concept_id)
    except ValueError:
        checks["catalog:concept_still_unregistered"] = True
    else:
        checks["catalog:concept_still_unregistered"] = False
    checks["catalog:measurement_kind_already_allowed"] = RESULT_KIND in {
        item["result_kind"] for item in catalog_raw["concepts"]
    }
    checks["catalog:no_new_result_kind"] = representation["concept"]["result_kind"] == "measurement"

    observed = []
    for case in synthetic["cases"]:
        trace = assess_close_path_envelope(case["closes"])
        source = trace.to_dict()
        payload = as_catalog_measurement(trace)
        expected_value = {
            "status": source["status"],
            "reason": source["reason"],
            "profile": source["profile"],
            "events": source["events"],
            "descriptors": source["descriptors"],
        }
        case_id = str(case["id"])
        checks[f"parity:{case_id}"] = payload["value"] == expected_value
        checks[f"digest:{case_id}"] = payload["value_digest"] == digest(expected_value)
        observed.append(
            {
                "id": case_id,
                "result_kind": payload["result_kind"],
                "value_digest": payload["value_digest"],
                "status": payload["value"]["status"],
            }
        )

    sample = as_catalog_measurement(assess_close_path_envelope(synthetic["cases"][0]["closes"]))
    revision = ObservationRevision(
        subject_id="sha256:" + "1" * 64,
        listing_context="listing-synthetic",
        concept_id=sample["concept_id"],
        concept_version=sample["concept_version"],
        spec_digest=CONTRACT_DIGEST,
        coordinate_ref="coordinate-synthetic",
        business_time=BusinessTime("measurement", datetime(2026, 9, 14, tzinfo=timezone.utc)),
        source_closure_digest="sha256:" + "2" * 64,
        value_digest=sample["value_digest"],
    )
    checks["object_model:structured_measurement_value_digest_supported"] = (
        revision.concept_id == concept_id and revision.value_digest == sample["value_digest"]
    )
    checks["authority:no_registration_or_market"] = (
        auth["authority"]["catalog_registration"] is False
        and auth["authority"]["market_data_read"] is False
        and representation["authority"]["catalog_registration"] is False
        and representation["authority"]["market_data_read"] is False
    )
    checks["representation:no_descriptor_fact_split"] = (
        representation["measurement_value_schema"]["events_are_independent_registered_facts"]
        is False
        and representation["measurement_value_schema"][
            "descriptors_are_independent_registered_facts"
        ]
        is False
    )

    failed = sorted(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": "quanthus.directional-path-envelope-e7-representation-result.v1",
        "work_item": "P3-STAGE-012-E7",
        "state": (
            "PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION"
            if not failed
            else "FAIL_REPRESENTATION_CONFORMANCE"
        ),
        "authorization_digest": AUTH_DIGEST,
        "representation_contract_digest": CONTRACT_DIGEST,
        "e5_synthetic_contract_digest": E5_SYNTHETIC_DIGEST,
        "active_catalog_digest": CATALOG_DIGEST,
        "active_b6_path_digest": B6_PATH_DIGEST,
        "checks": dict(sorted(checks.items())),
        "failed_checks": failed,
        "cases": observed,
        "claim_ceiling": (
            "Synthetic representation conformance only. The concept remains unregistered and no "
            "market/runtime/Stage qualification is claimed."
        ),
        "next_gate": {
            "work_item": "P3-STAGE-012-E8",
            "state": "READY_FOR_REGISTRATION_DECISION_REVIEW_ONLY",
            "purpose": (
                "Review whether the catalog-facing measurement representation should receive "
                "reserved semantic-catalog registration under B6-PATH; E8 itself may not mutate "
                "the catalog."

            ),
        },
        "authority": {
            "catalog_registration": False,
            "catalog_type_system_change": False,
            "active_b6_path_contract_change": False,
            "algorithm_change": False,
            "market_data_read": False,
            "runtime_job": False,
            "threshold_search": False,
            "candidate_generation": False,
            "preserved_2021_evaluation_read": False,
        },
    }


def main() -> int:
    print(json.dumps(run(), ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
