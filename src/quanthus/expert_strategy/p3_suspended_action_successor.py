"""Exact source-supported suspension reference successor; policies unchanged."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.backtest.contracts import CorporateAction
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_dated_share_successor as parent
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.expert_strategy import p3_tch_held_action_successor as predecessor
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
from quanthus.expert_strategy.p3_dated_share_engine import DatedShareEngine
from quanthus.expert_strategy.p3_optional_rights import no_subscription_disposition
from quanthus.pipelines.securities_warehouse import p10_rights_replay_input as rights_input
from quanthus.pipelines.securities_warehouse import p10_suspended_action_input as p10
from quanthus.pipelines.securities_warehouse.p10_rights_issue_source import load_rights_source

VERSION = "p3-suspended-action-reference-successor-v1"
REQUEST = "config/research/p3-strat-003/historical-suspended-action-successor.v1.json"
MEMBERS = {
    "src/quanthus/pipelines/securities_warehouse/p10_suspended_action_input.py",
    "src/quanthus/expert_strategy/p3_suspended_action_preparation.py",
    "src/quanthus/expert_strategy/p3_suspended_action_successor.py",
}
PRESERVED = (
    "scope",
    "arms",
    "execution_order",
    "time_blocks",
    "annual_diagnostics",
    "analysis",
    "input_preparation_request_digest",
    "optional_rights_policy",
    "optional_rights_policy_sha256",
    "reference_model",
    "share_availability_model",
    "share_policy",
)


def load_request(repository: Path) -> dict[str, Any]:
    old = predecessor.load_request(repository)
    r: dict[str, Any] = json.loads((repository / REQUEST).read_bytes())
    if (
        r.get("schema_version") != VERSION
        or r.get("parent_request_sha256") != native._sha(repository / predecessor.REQUEST)
        or set(r.get("code_config_members", {})) != MEMBERS
        or any(r.get(k) != old[k] for k in PRESERVED)
        or r.get("affected_security_ids") != ["002606"]
        or r.get("prior_affected_raw_cutoff") != "2026-10-24T16:49:27.317489+00:00"
        or r.get("official_source_manifest_sha256") != "sha256:" + p10.OFFICIAL_SHA
        or r.get("approved_proposal_sha256")
        != "sha256:bd0f33d0bedbf1a9f4901508b190c8c866aebaa690375cf4a620fa312681305a"
        or any(
            r.get(k) is not False
            for k in (
                "historical_PIT_claim",
                "production_authorized",
                "P6_mutation_authorized",
                "original_B_accepted",
                "original_D_activated",
                "parameter_search",
                "source_guards_waived",
            )
        )
        or r.get("maximum_additional_wall_seconds") != 7200
        or r.get("maximum_seconds_per_role") != 7200
        or r.get("previous_failures_costs_and_deadlines_retained") is not True
    ):
        raise ValueError("SUSPENDED_REQUEST")
    for name, identity in r["code_config_members"].items():
        if native._sha(native.physical(repository / name)) != identity:
            raise ValueError("SUSPENDED_CODE_CHANGED")
    return r


class SuspendedActionRuntime(parent.DatedShareRuntime):
    def __init__(self, repository: Path, source: Path, prepared: Path, admission: Any) -> None:
        self.historical_request = load_request(repository)
        self.request_digest = digest_value(self.historical_request)
        self.execution_request = self.historical_request
        self.execution_digest = self.request_digest
        self.effective_source_sha = self.historical_request["source_manifest_sha256"]
        if (
            admission.get("request_digest") != self.request_digest
            or admission.get("execution_digest") != self.request_digest
            or admission.get("role") not in {"primary", "independent_cold_reproduction"}
            or admission.get("source_manifest_sha256") != self.effective_source_sha
            or admission.get("preparation_sha256") != native._sha(prepared / "preparation.json")
            or admission.get("feature_sha256") != native._sha(prepared / "features.parquet")
        ):
            raise ValueError("SUSPENDED_EXACT_ADMISSION")
        self.bundle, self.official_facts = p10.load_input(
            source, self.effective_source_sha.removeprefix("sha256:")
        )
        self.prepared = prepared
        self.preparation = h.read_preparation(prepared / "preparation.json")
        if (
            self.preparation.get("action_successor_request_digest") != self.request_digest
            or self.preparation.get("effective_source_manifest_sha256") != self.effective_source_sha
            or self.preparation.get("source_information_cutoff")
            != self.historical_request["source_information_cutoff"]
        ):
            raise ValueError("SUSPENDED_PREREQUISITE")
        self.prerequisite_digest = digest_value(
            {k: v for k, v in self.preparation.items() if k != "feature_disposition"}
        )
        self.snapshot_id = VERSION + "/" + self.request_digest.removeprefix("sha256:")
        self.info = datetime.fromisoformat(self.historical_request["source_information_cutoff"])
        if self.info != self.bundle.historical_bundle.snapshot.knowledge_cutoff:
            raise ValueError("SUSPENDED_SOURCE_CLOCK")
        original = load_rights_source(
            source / "parent/parent/parent/parent/evidence/issuer", rights_input.ISSUER_SHA
        )
        self.rights_dispositions = [
            no_subscription_disposition(
                repository,
                original,
                arm=arm,
                market_date=original.ex_right_date,
                source_information_cutoff=self.info,
            )
            for arm in h.ARMS
        ]
        self.reader = parent.DatedShareReader(self.bundle, self.official_facts, self.info)
        self.entry_runners = {}
        try:
            FormalP2ReplayRuntime.__init__(
                self,
                repository,
                decision_start=date(2012, 1, 4),
                decision_end=date(2021, 12, 30),
                execution_end=date(2021, 12, 31),
                parent_event_run_digest=None,
                provider_config=VERSION,
            )
            self.stack.callback(self.reader.close)
            added = {
                "official-action:" + f.evidence_sha256: CorporateAction(
                    "official-action:" + f.evidence_sha256,
                    f.security_code,
                    f.ex_date,
                    f.acquired_at,
                    f.cash_per_share,
                    f.bonus_share_ratio,
                    f.source_revision + "|evidence=sha256:" + f.evidence_sha256,
                    self.effective_source_sha,
                )
                for f in self.official_facts
            }
            self.engine = DatedShareEngine(
                self.engine.policy, self.info, self.snapshot_id, added, self.official_facts
            )
        except BaseException:
            self.reader.close()
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _code_version(self) -> str:
        return super()._code_version() + ":" + VERSION

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "purpose": VERSION,
            "execution_adapter": VERSION,
        }
