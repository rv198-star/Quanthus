"""One bounded B-entry / held-risk control over the exposed historical fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_historical_controls as controls
from quanthus.expert_strategy.assessment_comparison import paired_intervals as paired_intervals
from quanthus.expert_strategy.p3_history_attribution import attribute
from quanthus.expert_strategy.p3_history_review import (
    ArchiveContract,
    ReviewedStudy,
    number,
    read_json,
    require,
)
from quanthus.expert_strategy.p3_restructuring_successor import RestructuringRuntime

VERSION = "p3-benchmark-held-v1"
REQUEST = "config/research/p3-strat-003/benchmark-held.v1.json"
COMPONENTS = {"BH": {"trend_entry": False, "entry_cap": False, "held_risk": True}}


def load_request(repository: Path) -> dict[str, Any]:
    parent = controls.load_request(repository)
    value, _ = read_json(repository / REQUEST)
    require(isinstance(value, dict), "BH_REQUEST_TYPE")
    request: dict[str, Any] = value
    require(
        request["schema_version"] == VERSION
        and request["parent_control_request_digest"] == digest_value(parent)
        and all(
            request[key] == parent[key]
            for key in (
                "parent_request_digest",
                "source_manifest_sha256",
                "source_information_cutoff",
                "scope",
                "optional_rights_parent_policy_sha256",
                "optional_paid_rights",
                "reuse",
            )
        )
        and request["components"] == COMPONENTS
        and request["entry_reference"] == "B"
        and request["held_reference"] == "TH"
        and request["candidate_keys"] == 22955
        and request["comparisons"] == [["BH", "B"], ["TH", "T"], ["TH", "BH"]]
        and request["parameter_search"] is False
        and all(
            request[key] is False
            for key in (
                "new_source_collection",
                "historical_PIT_claim",
                "production_authorized",
                "P6_mutation_authorized",
                "source_guards_waived",
                "independent_holdout_claim",
            )
        )
        and request["research_screen"]
        == {
            "net_total_return_delta": "0.01",
            "maximum_drawdown_delta": "0.01",
            "qualification": "DESCRIPTIVE_RESEARCH_CONTINUATION_ONLY",
        }
        and request["uncertainty"]
        == {
            "block_sessions": 22,
            "resamples": 2000,
            "confidence_level": "0.95",
            "seed_rule": "sha256(request_digest + ':' + comparison_id)",
            "incomplete_block": "OMIT_FROM_BOOTSTRAP_ONLY",
        },
        "BH_REQUEST",
    )
    return request


class BenchmarkHeldRuntime(controls.FixedControlRuntime):
    """Exactly B's entry; exactly TH's held-risk intervention dispatch."""

    control_version = VERSION
    component_spec = COMPONENTS

    @staticmethod
    def load_control_request(repository: Path) -> dict[str, Any]:
        return load_request(repository)

    def _held_intervention_id(self, intervention_id: str | None) -> None:
        self._validate_intervention_id(intervention_id)
        return None

    @staticmethod
    def _variant_id(intervention_id: str | None) -> str:
        require(intervention_id in COMPONENTS, "CONTROL_ARM")
        return "variant." + str(intervention_id)

    def _evaluate_entry_decision(
        self, runner: Any, *, intervention_id: str | None, **kwargs: Any
    ) -> Any:
        self._validate_intervention_id(intervention_id)
        return RestructuringRuntime._evaluate_entry_decision(
            self, runner, intervention_id="B", **kwargs
        )


def review_benchmark_held(
    freeze: dict[str, Any], trusted_anchor: dict[str, Any] | None = None
) -> ReviewedStudy:
    return controls._review_fixed_cell(
        freeze, version=VERSION, components=COMPONENTS, archive_anchor=trusted_anchor
    )


def analyze(
    study: ReviewedStudy, contract: ArchiveContract, request: dict[str, Any]
) -> dict[str, Any]:
    """One predeclared four-cell decision, not strategy promotion or a holdout."""
    require({"B", "T", "TH", "BH"} <= set(study.primary), "BH_ANALYSIS_ARMS")
    comparisons = tuple(tuple(pair) for pair in request["comparisons"])
    result = attribute(study, contract, comparisons=comparisons)
    request_digest = digest_value(request)
    intervals = {}
    dates = [r["market_date"] for r in study.primary["B"]["daily-returns.json"]]
    capital = number(contract.initial_unit_capital) * len(contract.unit_ids)
    for arm, data in study.primary.items():
        require([r["market_date"] for r in data["daily-returns.json"]] == dates, "BH_DATE_PAIRING")
        returns = sorted(number(r["return"]) for r in data["daily-returns.json"])
        count = (len(returns) + 19) // 20
        fills = [e["payload"] for e in data["engine-events.json"] if e["event_type"] == "fill"]
        result["variants"][arm].update(
            {
                "daily_tail_mean_worst_5pct": str(sum(returns[:count], Decimal(0)) / count),
                "worst_daily_return": str(returns[0]),
                "time_in_market": str(
                    sum(number(r["market_value"]) > 0 for r in data["equity.json"][1:])
                    / Decimal(len(dates))
                ),
                "turnover_on_initial_capital": str(
                    sum((number(r["gross_amount"]) for r in fills), Decimal(0)) / capital
                ),
            }
        )
    for left, right in comparisons:
        key = left + "-" + right
        seed = int(hashlib.sha256((request_digest + ":" + key).encode()).hexdigest(), 16)
        intervals[key] = {
            "seed": str(seed),
            "point_delta": {
                k: str(number(study.summaries[left][k]) - number(study.summaries[right][k]))
                for k in ("net_total_return", "maximum_drawdown")
            },
            "descriptive_unadjusted_95_interval": paired_intervals(
                [float(r["return"]) for r in study.primary[left]["daily-returns.json"]],
                [float(r["return"]) for r in study.primary[right]["daily-returns.json"]],
                seed,
            ),
        }
    result.update(
        {
            "purpose": VERSION,
            "request_digest": request_digest,
            "new_replay": True,
            "paired_intervals": intervals,
            "complete_22_session_blocks": len(dates) // 22,
            "incomplete_dates_excluded_only_from_resampling": dates[len(dates) // 22 * 22 :],
            "interaction_TH_minus_T_minus_BH_plus_B": {
                k: str(
                    number(study.summaries["TH"][k])
                    - number(study.summaries["T"][k])
                    - number(study.summaries["BH"][k])
                    + number(study.summaries["B"][k])
                )
                for k in ("net_total_return", "maximum_drawdown")
            },
            "historical_PIT_claim": False,
            "independent_holdout_claim": False,
            "strategy_or_production_accepted": False,
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument(
        "--role", choices=("primary", "independent_cold_reproduction"), required=True
    )
    args = parser.parse_args()
    freeze, _ = read_json(args.freeze)
    result = controls._run_fixed_role(freeze, args.role, BenchmarkHeldRuntime)
    print(
        json.dumps(
            {
                "stage": "role_complete",
                "role": args.role,
                "bytes": result["bytes"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
