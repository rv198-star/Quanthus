"""One explicit, source-bound successor to the unchanged historical comparison.

P10 owns the qualified one-key execution input. P3 retains the frozen scientific
window, arms, candidate rules, costs, accounting and every remaining data guard.
The no-subscription policy is identical for B/T/TCH; it never creates a free share
or compensating cash flow. This is retrospective development, not historical PIT.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
from quanthus.expert_strategy.p3_optional_rights import POLICY, no_subscription_disposition
from quanthus.pipelines.securities_warehouse import p10_rights_replay_input as p10
from quanthus.pipelines.securities_warehouse.p10_rights_issue_source import load_rights_source

VERSION = "p3-historical-rights-successor-v1"
REQUEST = "config/research/p3-strat-003/historical-rights-successor.v1.json"
MEMBERS = {
    "src/quanthus/expert_strategy/p3_historical_rights_successor.py",
    "src/quanthus/expert_strategy/p3_optional_rights.py",
    "src/quanthus/pipelines/securities_warehouse/p10_rights_issue_source.py",
    "src/quanthus/pipelines/securities_warehouse/p10_rights_issue_reference.py",
    "src/quanthus/pipelines/securities_warehouse/p10_rights_replay_input.py",
    POLICY,
}


def load_request(repository: Path) -> dict[str, Any]:
    parent = h.load_request(repository)
    request: dict[str, Any] = json.loads((repository / REQUEST).read_bytes())
    if (
        request.get("schema_version") != VERSION
        or request.get("parent_request_sha256") != native._sha(repository / h.REQUEST)
        or request.get("parent_source_manifest_sha256") != h.SOURCE_SHA
        or request.get("optional_rights_policy_sha256") != native._sha(repository / POLICY)
        or request.get("qualified_reference_manifest_sha256") != "sha256:" + p10.PRODUCT_SHA
        or request.get("source_manifest_sha256")
        != "sha256:59e3cc21e00e56c087e3a8d1f2ff1c413fad8b1eaceb3caf3c4e93d1c9f740d7"
        or request.get("source_information_cutoff") != "2026-10-24T13:13:38.139930+00:00"
        or request.get("optional_rights_policy") != "DO_NOT_SUBSCRIBE_UNIFORMLY"
        or request.get("source_repair_scope") != [["000901", "2014-10-24"]]
        or any(
            request.get(k) != parent[k]
            for k in (
                "scope",
                "arms",
                "execution_order",
                "time_blocks",
                "annual_diagnostics",
                "analysis",
                "input_preparation_request_digest",
                "action_source_snapshot_id",
            )
        )
        or any(
            request.get(k) is not False
            for k in (
                "historical_PIT_claim",
                "independent_holdout_claim",
                "production_authorized",
                "original_B_accepted",
                "original_D_activated",
                "P6_mutation_authorized",
                "parameter_search",
                "source_guards_waived",
                "free_rights_or_dilution_compensation",
            )
        )
        or set(request.get("code_config_members", {})) != MEMBERS
    ):
        raise native.InputError("RIGHTS_SUCCESSOR_REQUEST")
    for name, sha in request["code_config_members"].items():
        if native._sha(native.physical(repository / name)) != sha:
            raise native.InputError("RIGHTS_SUCCESSOR_CODE_CHANGED")
    return request


def prepare(
    repository: Path, source: Path, output: Path, request: dict[str, Any]
) -> dict[str, Any]:
    """Build one immutable feature/candidate prerequisite per independent role."""
    verified = load_request(repository)
    if request != verified:
        raise native.InputError("RIGHTS_SUCCESSOR_REQUEST_ARGUMENT")
    bundle = p10.load_input(source, request["source_manifest_sha256"].removeprefix("sha256:"))
    # The original producer's raw-candidate namespace stays fixed. Its raw bar,
    # status, listing and industry inputs are unchanged; the new feature identity
    # separately binds the complete effective source and actual information time.
    parent = h.load_request(repository)
    parent["source_information_cutoff"] = request["source_information_cutoff"]
    result = h.prepare(repository, bundle, output, parent)
    result["rights_successor_request_digest"] = digest_value(request)
    result["effective_source_manifest_sha256"] = bundle.manifest_sha256
    result["source_information_cutoff"] = request["source_information_cutoff"]
    result["optional_rights_policy_sha256"] = request["optional_rights_policy_sha256"]
    (output / "preparation.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


class HistoricalRightsRuntime(h.HistoricalRuntime):
    def __init__(
        self, repository: Path, source: Path, prepared: Path, admission: dict[str, Any]
    ) -> None:
        self.historical_request = load_request(repository)
        self.request_digest = digest_value(self.historical_request)
        self.effective_source_sha = self.historical_request["source_manifest_sha256"]
        if (
            admission.get("request_digest") != self.request_digest
            or admission.get("role") not in {"primary", "independent_cold_reproduction"}
            or admission.get("source_manifest_sha256") != self.effective_source_sha
            or admission.get("preparation_sha256") != native._sha(prepared / "preparation.json")
            or admission.get("feature_sha256") != native._sha(prepared / "features.parquet")
        ):
            raise native.InputError("RIGHTS_SUCCESSOR_EXACT_ADMISSION")
        self.bundle = p10.load_input(source, self.effective_source_sha.removeprefix("sha256:"))
        self.prepared = prepared
        self.preparation = h.read_preparation(prepared / "preparation.json")
        if (
            self.preparation.get("request_digest") != h.POPULATION_REQUEST_DIGEST
            or self.preparation.get("rights_successor_request_digest") != self.request_digest
            or self.preparation.get("effective_source_manifest_sha256") != self.effective_source_sha
            or self.preparation.get("source_information_cutoff")
            != self.historical_request["source_information_cutoff"]
            or self.preparation.get("optional_rights_policy_sha256")
            != self.historical_request["optional_rights_policy_sha256"]
        ):
            raise native.InputError("RIGHTS_SUCCESSOR_PREREQUISITE")
        self.prerequisite_digest = digest_value(
            {k: v for k, v in self.preparation.items() if k != "feature_disposition"}
        )
        self.snapshot_id = VERSION + "/" + self.request_digest.removeprefix("sha256:")
        self.info = datetime.fromisoformat(self.historical_request["source_information_cutoff"])
        facts = load_rights_source(source / "evidence/issuer", p10.ISSUER_SHA)
        self.rights_dispositions = [
            no_subscription_disposition(
                repository,
                facts,
                arm=arm,
                market_date=facts.ex_right_date,
                source_information_cutoff=self.info,
            )
            for arm in h.ARMS
        ]
        self.reader = h.BoundedHistoricalReader(self.bundle)
        self.entry_runners: dict[str, Any] = {}
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
            if self.reader._rows(
                "SELECT DISTINCT source_snapshot_id FROM replay_actions ORDER BY source_snapshot_id"
            ) != [{"source_snapshot_id": h.ACTION_SOURCE_SNAPSHOT}]:
                raise native.InputError("RIGHTS_SUCCESSOR_ACTION_SOURCE")
            self.engine = h.HistoricalEngine(
                self.engine.policy, self.info, self.snapshot_id, h.ACTION_SOURCE_SNAPSHOT
            )
        except BaseException:
            self.reader.close()
            if hasattr(self, "stack"):
                self.stack.close()
            raise

    def _load_event_population(self, event_root: Path) -> Any:
        events, mapping = super()._load_event_population(event_root)
        for event in events:
            event.record = replace(event.record, snapshot_manifest_digest=self.effective_source_sha)
        return events, mapping

    def _open_data_inputs(self) -> None:
        super()._open_data_inputs()
        self.data_manifest_sha256 = self.effective_source_sha
        self.frames = [
            replace(
                frame, snapshot=replace(frame.snapshot, source_revision=self.effective_source_sha)
            )
            for frame in self.frames
        ]

    def _parameter_payload(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._parameter_payload(intervention_id),
            "source_manifest_sha256": self.effective_source_sha,
            "parent_source_manifest_sha256": h.SOURCE_SHA,
            "optional_rights_policy_sha256": self.historical_request[
                "optional_rights_policy_sha256"
            ],
        }

    def _population_manifest_fields(self) -> dict[str, Any]:
        return {
            **super()._population_manifest_fields(),
            "source_manifest_sha256": self.effective_source_sha,
            "raw_candidate_parent_source_manifest_sha256": h.SOURCE_SHA,
            "rights_policy_dispositions_digest": digest_value(self.rights_dispositions),
        }

    def _manifest_metadata(self, intervention_id: str | None) -> dict[str, Any]:
        return {
            **super()._manifest_metadata(intervention_id),
            "purpose": VERSION,
            "shared_execution_kernel": h.VERSION,
            "source_information_cutoff": self.info.isoformat(),
            "optional_rights_policy_sha256": self.historical_request[
                "optional_rights_policy_sha256"
            ],
            "no_subscription_all_arms": True,
            "original_request_and_failure_preserved": True,
        }
