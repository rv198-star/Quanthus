#!/usr/bin/env python3
"""Reproduce the #545 offline census from an externally frozen selected file set."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
from dataclasses import asdict
from datetime import date, time
from itertools import combinations
from pathlib import Path
from time import perf_counter
from typing import Any

from quanthus.expert_strategy.held_structure_preflight import (
    Population,
    PreflightError,
    compare_identity_paths,
    read_pinned_json,
    recover_population,
    screen_candidate,
)

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config/research/expert545-held-preflight.v1.json"


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode()


def run(selection_path: Path, event_selection_path: Path, output: Path) -> dict[str, Any]:
    started = perf_counter()
    config = json.loads(CONFIG.read_bytes())
    selection = json.loads(selection_path.read_bytes())
    event_selection = json.loads(event_selection_path.read_bytes())
    if (
        event_selection["parent_selection_sha256"]
        != hashlib.sha256(selection_path.read_bytes()).hexdigest()
    ):
        raise PreflightError("selection_parent_mismatch")
    members = [*selection["members"], *event_selection["members"]]
    paths = [Path(m["path"]).resolve() for m in members]
    if len(paths) != len(set(paths)):
        raise PreflightError("duplicate_selected_path")
    if sum(m["size_bytes"] for m in members) > config["maximum_selected_total_bytes"]:
        raise PreflightError("selected_total_exceeded")
    if any(m["size_bytes"] > config["maximum_selected_file_bytes"] for m in members):
        raise PreflightError("selected_file_limit_exceeded")
    output = output.resolve()
    if any(output == p.parent or output.is_relative_to(p.parent) for p in paths):
        raise PreflightError("output_inside_selected_source")
    if output.exists():
        raise PreflightError("output_already_exists")
    if shutil.disk_usage(output.parent).free < config["minimum_free_bytes"]:
        raise PreflightError("insufficient_free_space")
    verified: set[str] = set()

    def load(suffix: str) -> Any:
        matches = [m for m in members if m["path"].endswith(suffix)]
        if len(matches) != 1:
            raise PreflightError("selected_member_not_unique:" + suffix)
        m = matches[0]
        result = read_pinned_json(Path(m["path"]), m["sha256"], maximum_bytes=m["size_bytes"])
        verified.add(m["path"])
        return result

    coordinates = load("/event-coordinates.json")
    populations: list[Population] = []
    for arm in ("B", "BH", "TH"):
        decisions = load(f"/variant.{arm}/decisions.json")
        events = load(f"/variant.{arm}/engine-events.json")
        populations.append(
            recover_population(
                arm,
                decisions,
                events,
                coordinates,
                first_date=date.fromisoformat(config["decision_start"]),
                last_date=date.fromisoformat(config["decision_end"]),
                decision_time=time.fromisoformat(config["decision_time_asia_shanghai"]),
            )
        )
        del decisions, events
    materialize_elapsed = perf_counter() - started
    factors = load("/facts-factor.private.json")
    screens = [screen_candidate(population, factors) for population in populations]
    del factors
    for member in members:
        if member["path"] not in verified:
            read_pinned_json(
                Path(member["path"]), member["sha256"], maximum_bytes=member["size_bytes"]
            )
            verified.add(member["path"])
    blockers = []
    if not any(screen["coarse_code_date_overlap"] for screen in screens):
        blockers.append("NO_COARSE_DECISION_FACT_OVERLAP")
    if any(screen["missing_support_fields"] for screen in screens):
        blockers.append("CANDIDATE_IS_NOT_SUPPORT_EVOLUTION_FACTS")
    for name in (
        "price_basis_bridge_qualified",
        "independent_usefulness_reference_qualified",
        "candidate_policy_mapping_frozen",
    ):
        if config[name] is not True:
            blockers.append(name.upper() + "_MISSING")

    tmp = _bytes(
        {
            "kind": "expert545-held-identity-TMP-v1",
            "lifecycle": "job_local_evidence_not_ADM_or_consumer_input",
            "rows": {p.arm: [asdict(row) for row in p.identities] for p in populations},
        }
    )
    code_paths = [
        Path(__file__),
        REPO / "src/quanthus/expert_strategy/held_structure_preflight.py",
        REPO / "src/quanthus/expert_strategy/position_risk.py",
        CONFIG,
        REPO / config["policy_config"],
    ]
    identities = {
        str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in code_paths
    }
    report = {
        "kind": "expert545-held-structure-preflight-safe-v1",
        "scope": config["purpose"],
        "config_and_code_sha256": identities,
        "selected_inputs": [
            {"path": m["path"], "sha256": m["sha256"], "size_bytes": m["size_bytes"]}
            for m in members
        ],
        "populations": [p.safe_summary() for p in populations],
        "identity_path_screens": [
            compare_identity_paths(a, b) for a, b in combinations(populations, 2)
        ],
        "factor_fixture_screens": screens,
        "shared_intermediate": {
            "sha256": hashlib.sha256(tmp).hexdigest(),
            "bytes": len(tmp),
            "materializations": 1,
            "consumers": ["input_admission", "decision_footprint"],
            "full_consumer_state": False,
        },
        "decision": (
            "STOP_NO_ADMISSIBLE_DECISION_FACT_PAIR"
            if blockers
            else "SEPARATE_COMPARISON_REQUEST_REQUIRED"
        ),
        "blockers": blockers,
        "verified_selected_members": len(verified),
        "comparison": "NOT_RUN",
        "potential_changed_decisions": None,
        "measured_effect_or_economic_value": None,
        "independent_usefulness_reference_qualified": False,
        "candidate_policy_mapping_frozen": False,
        "historical_PIT_claim": False,
        "production_or_P6_authorized": False,
    }
    report_bytes = _bytes(report)
    if len(tmp) + len(report_bytes) > config["maximum_output_bytes"]:
        raise PreflightError("output_budget_exceeded")
    output.mkdir()
    for name, payload in [
        ("held-identities.TMP.private.json", tmp),
        ("decision-footprint.safe.json", report_bytes),
    ]:
        target = output / name
        with target.open("xb") as stream:
            stream.write(payload)
        target.chmod(0o440)
    receipt = {
        "elapsed_seconds": perf_counter() - started,
        "materialize_seconds": materialize_elapsed,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "written_bytes_before_receipt": len(tmp) + len(report_bytes),
        "read_disposition": "one immutable identity materialization; both consumers reuse it",
        "prices_returns_future_targets_selected": False,
    }
    (output / "execution.safe.json").write_bytes(_bytes(receipt))
    return {
        "decision": report["decision"],
        "comparison": report["comparison"],
        "output": str(output),
        **receipt,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--event-selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.selection, args.event_selection, args.output)))


if __name__ == "__main__":
    main()
