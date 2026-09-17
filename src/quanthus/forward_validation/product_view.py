"""Read-only P6 Forward/Paper projection for the candidate-detail product surface.

The view validates the exact frozen P6 predecessor artifacts before projecting them. It never reads
DWD, never creates a paper ledger, never submits a Job, and exposes no activation or execution
command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from quanthus.forward_validation.contracts import ForwardValidationContractError, fail
from quanthus.forward_validation.data_boundary import load_data_boundary_policy
from quanthus.forward_validation.paper_ledger import load_paper_policy
from quanthus.forward_validation.runner import load_forward_worker_config
from quanthus.forward_validation.scorecards import load_scorecard_policy
from quanthus.forward_validation.validation import load_entry_proposal
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB
from quanthus.platform.persistence import PlatformStore

_SCHEMA = "quanthus.p6-forward-product-view.v1"
_TARGET_STRATEGY = "candidate.p4-trend-risk"
_TARGET_VERSION = "1.0.1"
_TARGET_DIGEST = "sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardValidationContractError("forward_product_view_invalid_json", str(path)) from exc
    if not isinstance(value, dict):
        fail("field_mismatch", f"{path} must contain one object")
    return value


def _bound(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, Mapping):
        fail("field_mismatch", f"{field} must be an object")
    raw = value.get("path")
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        fail("path_escape", f"{field}.path")
    path = (root / raw).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        fail("binding_missing", field)
    expected = value.get("sha256")
    if expected != _sha(path):
        fail("binding_digest_drift", field)
    return path


@dataclass(frozen=True)
class P6ForwardProductViewConfig:
    path: Path
    config_sha256: str
    strategy_card: Path
    entry_proposal: Path
    data_boundary: Path
    scorecards: Path
    forward_worker: Path
    paper_lane: Path
    predecessor_acceptance: Mapping[str, Path]


def load_forward_product_view_config(path: Path, *, root: Path) -> P6ForwardProductViewConfig:
    raw = _load(path)
    if (
        raw.get("schema_version") != _SCHEMA
        or raw.get("work_item") != "P6-WEB-001"
        or raw.get("state") != "FROZEN_READ_ONLY_PRE_ACTIVATION"
    ):
        fail("forward_product_view_drift", "P6 product-view identity differs")
    candidate = raw.get("candidate")
    if not isinstance(candidate, dict) or (
        candidate.get("strategy_id") != _TARGET_STRATEGY
        or candidate.get("strategy_version") != _TARGET_VERSION
        or candidate.get("strategy_semantic_digest") != _TARGET_DIGEST
    ):
        fail("forward_product_view_drift", "candidate binding differs")
    strategy_card = _bound(root, candidate.get("strategy_card"), "candidate.strategy_card")
    strategy = _load(strategy_card)
    if strategy.get("semantic_digest") != _TARGET_DIGEST:
        fail("forward_product_view_drift", "strategy semantic digest differs")

    bindings = raw.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != {
        "entry_proposal",
        "data_boundary",
        "scorecards",
        "forward_worker",
        "paper_lane",
    }:
        fail("field_mismatch", "forward product bindings differ")
    predecessor = raw.get("accepted_predecessors")
    if not isinstance(predecessor, dict) or set(predecessor) != {
        "P6-GOV-001",
        "P6-DATA-001",
        "P6-SCORE-001",
        "P6-GOV-002",
        "P6-PAPER-001",
    }:
        fail("field_mismatch", "accepted predecessor set differs")
    predecessor_paths = {
        key: _bound(root, value, f"accepted_predecessors.{key}")
        for key, value in sorted(predecessor.items())
    }

    projection = raw.get("projection")
    if not isinstance(projection, dict) or projection != {
        "candidate_detail_only": True,
        "read_only": True,
        "show_admission_state": True,
        "show_evidence_state": True,
        "show_budget": True,
        "show_latest_forward_job": True,
        "show_scorecard_contracts": True,
        "show_paper_policy": True,
        "show_source_digests": True,
        "show_activation_boundary": True,
        "show_notification_or_broker_controls": False,
        "show_parameter_edit_controls": False,
        "show_activation_control": False,
    }:
        fail("forward_product_view_drift", "read-only projection contract differs")
    authority = raw.get("authority")
    if not isinstance(authority, dict) or authority.get("web_projection_implementation_authorized") is not True:
        fail("authority_drift", "P6 Web implementation authority missing")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "web_projection_implementation_authorized"
    ):
        fail("authority_drift", "P6 Web view grants downstream authority")

    return P6ForwardProductViewConfig(
        path=path,
        config_sha256=_sha(path),
        strategy_card=strategy_card,
        entry_proposal=_bound(root, bindings["entry_proposal"], "bindings.entry_proposal"),
        data_boundary=_bound(root, bindings["data_boundary"], "bindings.data_boundary"),
        scorecards=_bound(root, bindings["scorecards"], "bindings.scorecards"),
        forward_worker=_bound(root, bindings["forward_worker"], "bindings.forward_worker"),
        paper_lane=_bound(root, bindings["paper_lane"], "bindings.paper_lane"),
        predecessor_acceptance=predecessor_paths,
    )


class ForwardValidationProductView:
    """Validated read-only product projection for the frozen P6 candidate."""

    def __init__(
        self,
        root: Path,
        store: PlatformStore,
        config_path: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.store = store
        self.config = load_forward_product_view_config(
            config_path
            or self.root / "config/forward-validation/web/p6-forward-product-view.v1.json",
            root=self.root,
        )
        self.entry = load_entry_proposal(self.config.entry_proposal, root=self.root)
        self.data = load_data_boundary_policy(self.config.data_boundary, root=self.root)
        self.scorecards = load_scorecard_policy(self.config.scorecards)
        self.worker = load_forward_worker_config(self.config.forward_worker, root=self.root)
        self.paper = load_paper_policy(self.config.paper_lane, root=self.root)
        self._entry_raw = _load(self.config.entry_proposal)

    def _latest_job(self) -> dict[str, object] | None:
        jobs = self.store.list_jobs(P6_FORWARD_CYCLE_JOB, 1)
        if not jobs:
            return None
        job = jobs[0]
        summary = None
        if isinstance(job.result_ref, Mapping):
            value = job.result_ref.get("summary")
            if isinstance(value, Mapping):
                summary = dict(value)
        return {
            "job_id": job.job_id,
            "state": job.state,
            "stage": job.stage,
            "progress": job.progress,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "completed_at": None if job.completed_at is None else job.completed_at.isoformat(),
            "run_id": None if not isinstance(job.result_ref, Mapping) else job.result_ref.get("run_id"),
            "summary": summary,
        }

    def candidate(self, strategy_id: str, strategy_version: str) -> dict[str, object]:
        if (strategy_id, strategy_version) != (_TARGET_STRATEGY, _TARGET_VERSION):
            return {"state": "not_applicable", "read_only": True}

        budget = self._entry_raw["observation_budget"]
        activation = self._entry_raw["activation_boundary"]
        authority = self._entry_raw["authority"]
        latest_job = self._latest_job()
        scorecard_roles = {
            role: {
                "source": value.source,
                "primary_metrics": list(value.primary_metrics),
                "guardrails": list(value.guardrails),
            }
            for role, value in sorted(self.scorecards.roles.items())
        }
        source_digests = {
            "web_projection": self.config.config_sha256,
            "entry_proposal": _sha(self.config.entry_proposal),
            "data_boundary": self.data.policy_sha256,
            "scorecard_policy": self.scorecards.policy_sha256,
            "forward_worker": self.worker.config_sha256,
            "paper_lane": self.paper.policy_sha256,
            **{
                f"acceptance.{key}": _sha(path)
                for key, path in sorted(self.config.predecessor_acceptance.items())
            },
        }
        return {
            "state": "engineering_ready_activation_disabled",
            "read_only": True,
            "strategy_binding": {
                "strategy_id": _TARGET_STRATEGY,
                "strategy_version": _TARGET_VERSION,
                "strategy_semantic_digest": _TARGET_DIGEST,
            },
            "admission": {
                "state": "disabled",
                "activation_owner": activation["activation_owner"],
                "first_official_market_date_rule": activation["first_official_market_date_rule"],
                "required_release_state": activation["required_release_state"],
                "official_prospective_cycle_authorized": bool(
                    authority["official_prospective_cycle_authorized"]
                ),
            },
            "evidence": {
                "state": "waiting_activation",
                "official_collection_sessions_completed": 0,
                "independent_blocks_completed": 0,
                "settled_target_count": 0,
                "censored_target_count": 0,
                "first_official_market_date": None,
                "last_admitted_dwd_release": None,
            },
            "budget": {
                "independent_unit": budget["independent_unit"],
                "collection_block_count": budget["collection_block_count"],
                "sessions_per_block": budget["sessions_per_block"],
                "collection_trading_sessions": budget["collection_trading_sessions"],
                "target_maturity_tail_trading_sessions": budget[
                    "target_maturity_tail_trading_sessions"
                ],
                "maximum_calendar_days": budget["maximum_calendar_days"],
                "cross_section_increases_independent_progress": budget[
                    "cross_section_increases_independent_progress"
                ],
            },
            "latest_forward_job": latest_job,
            "scorecards": {
                "state": "frozen_pre_result",
                "roles": scorecard_roles,
                "material_loss_threshold_cny": str(
                    self.scorecards.material_loss_threshold_cny
                ),
                "observed_scorecards": [],
            },
            "paper": {
                "state": "engineering_ready_not_active",
                "authority": "paper",
                "actual_execution_authorized": False,
                "execution_profile": self.paper.execution_policy.active_profile.profile_id,
                "unit_count": self.paper.unit_count,
                "unit_notional_cny": str(self.paper.unit_notional),
                "configured_total_notional_cny": str(
                    self.paper.unit_notional * self.paper.unit_count
                ),
                "compound_capital": False,
                "profit_redeployment": False,
                "ledger_head": None,
                "cash_cny": None,
                "positions": [],
                "pending_intents": [],
                "fills": [],
                "reconciled": None,
            },
            "authority": {
                "notification_authorized": False,
                "broker_authorized": False,
                "live_capital_authorized": False,
                "stage_action_authority": False,
                "parameter_edit_authorized": False,
                "activation_control_exposed": False,
            },
            "source_digests": source_digests,
            "limitations": [
                "No official prospective observation exists before a separate P6-ACTIVATE-001 decision.",
                "Pre-activation 2026 history is engineering rehearsal only and cannot increase budget progress.",
                "Arbitration 2022-2024 and permanent blind 2025 remain sealed.",
                "The current page is read-only and exposes no activation, broker, notification, live-capital or parameter-edit authority.",
            ],
        }
