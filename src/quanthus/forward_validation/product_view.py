"""Lean read-only P6 Forward/Paper projection for candidate detail.

This module is intentionally independent from DWD, P2 and Forward Worker runtime modules so the
Platform API image can display frozen P6 state without gaining data-analysis or execution
capability. Runtime configs are validated as immutable JSON artifacts; acceptance-document hashes
remain provenance metadata and are checked by repository tests when those documents are present.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn, cast

from quanthus.forward_validation.activation import (
    activation_decision_from_mapping,
    load_activation_decision,
    load_owner_activation_mandate,
    load_prospective_runtime_config,
)
from quanthus.forward_validation.base24_contract import load_runtime as load_base24_runtime
from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.platform.jobs import P6_FORWARD_CYCLE_JOB
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import PlatformStore

_SCHEMA = "quanthus.p6-forward-product-view.v1"
_TARGET_STRATEGY = "candidate.p4-trend-risk"
_TARGET_VERSION = "1.0.1"
_TARGET_DIGEST = "sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c"
_ENTRY_PROPOSAL_DIGEST = "sha256:3c9356e49dbdd6356754b42cfd34af11949289121454a05bdbf0c0ff2aebaa18"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ForwardProductViewError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason: str, detail: str) -> NoReturn:
    raise ForwardProductViewError(reason, detail)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardProductViewError("forward_product_view_invalid_json", str(path)) from exc
    if not isinstance(value, dict):
        _fail("field_mismatch", f"{path} must contain one object")
    return cast(dict[str, Any], value)


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail("invalid_digest", field)
    return value


def _relative_path(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value:
        _fail("field_mismatch", f"{field}.path must be text")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        _fail("path_escape", f"{field}.path")
    resolved = (root / relative).resolve()
    if root.resolve() not in resolved.parents:
        _fail("path_escape", f"{field}.path")
    return resolved


def _runtime_binding(root: Path, value: object, field: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        _fail("field_mismatch", f"{field} must contain path/sha256")
    path = _relative_path(root, value["path"], field)
    if not path.is_file():
        _fail("binding_missing", field)
    expected = _digest(value["sha256"], f"{field}.sha256")
    if _sha(path) != expected:
        _fail("binding_digest_drift", field)
    return path, _load(path)


def _provenance_binding(root: Path, value: object, field: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        _fail("field_mismatch", f"{field} must contain path/sha256")
    path_text = value["path"]
    path = _relative_path(root, path_text, field)
    expected = _digest(value["sha256"], f"{field}.sha256")
    # Acceptance prose is intentionally absent from the lean production image.  When it is present
    # in a source checkout, require exact bytes; otherwise preserve the frozen digest as provenance.
    if path.is_file() and _sha(path) != expected:
        _fail("binding_digest_drift", field)
    return str(path_text), expected


@dataclass(frozen=True)
class P6ForwardProductViewConfig:
    path: Path
    config_sha256: str
    strategy_card_sha256: str
    entry_sha256: str
    data_sha256: str
    score_sha256: str
    worker_sha256: str
    paper_sha256: str
    entry: Mapping[str, Any]
    data: Mapping[str, Any]
    score: Mapping[str, Any]
    worker: Mapping[str, Any]
    paper: Mapping[str, Any]
    predecessor_acceptance: Mapping[str, tuple[str, str]]


def load_forward_product_view_config(path: Path, *, root: Path) -> P6ForwardProductViewConfig:
    root = root.resolve()
    raw = _load(path)
    if (
        raw.get("schema_version") != _SCHEMA
        or raw.get("work_item") != "P6-WEB-001"
        or raw.get("state") != "FROZEN_READ_ONLY_PRE_ACTIVATION"
    ):
        _fail("forward_product_view_drift", "P6 product-view identity differs")

    candidate = raw.get("candidate")
    if not isinstance(candidate, dict) or (
        candidate.get("strategy_id") != _TARGET_STRATEGY
        or candidate.get("strategy_version") != _TARGET_VERSION
        or candidate.get("strategy_semantic_digest") != _TARGET_DIGEST
    ):
        _fail("forward_product_view_drift", "candidate binding differs")
    card_path, card = _runtime_binding(
        root,
        candidate.get("strategy_card"),
        "candidate.strategy_card",
    )
    if card.get("semantic_digest") != _TARGET_DIGEST:
        _fail("forward_product_view_drift", "strategy semantic digest differs")

    bindings = raw.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != {
        "entry_proposal", "data_boundary", "scorecards", "forward_worker", "paper_lane"
    }:
        _fail("field_mismatch", "forward product bindings differ")
    entry_path, entry = _runtime_binding(
        root,
        bindings["entry_proposal"],
        "bindings.entry_proposal",
    )
    data_path, data = _runtime_binding(root, bindings["data_boundary"], "bindings.data_boundary")
    score_path, score = _runtime_binding(root, bindings["scorecards"], "bindings.scorecards")
    worker_path, worker = _runtime_binding(
        root,
        bindings["forward_worker"],
        "bindings.forward_worker",
    )
    paper_path, paper = _runtime_binding(root, bindings["paper_lane"], "bindings.paper_lane")

    if (
        entry.get("schema_version") != "quanthus.p6-entry-proposal.v1"
        or entry.get("proposal_digest") != _ENTRY_PROPOSAL_DIGEST
        or entry.get("state") != "FROZEN_ENTRY_ONLY_NO_ACTIVATION"
    ):
        _fail("forward_product_view_drift", "entry proposal identity differs")
    if data.get("schema_version") != "quanthus.p6-prospective-data-boundary.v1":
        _fail("forward_product_view_drift", "data-boundary identity differs")
    if score.get("schema_version") != "quanthus.p6-role-scorecard-policy.v1":
        _fail("forward_product_view_drift", "scorecard identity differs")
    if worker.get("schema_version") != "quanthus.p6-forward-worker.v1":
        _fail("forward_product_view_drift", "Forward Worker identity differs")
    if (
        paper.get("schema_version") != "quanthus.p6-paper-lane.v1"
        or paper.get("work_item") != "P6-PAPER-001"
        or paper.get("state") != "FROZEN_ENGINEERING_ONLY_NO_ACTIVATION"
    ):
        _fail("forward_product_view_drift", "Paper policy identity differs")

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
        _fail("forward_product_view_drift", "read-only projection contract differs")
    authority = raw.get("authority")
    if (
        not isinstance(authority, dict)
        or authority.get("web_projection_implementation_authorized") is not True
    ):
        _fail("authority_drift", "P6 Web implementation authority missing")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "web_projection_implementation_authorized"
    ):
        _fail("authority_drift", "P6 Web view grants downstream authority")

    predecessor = raw.get("accepted_predecessors")
    if not isinstance(predecessor, dict) or set(predecessor) != {
        "P6-GOV-001", "P6-DATA-001", "P6-SCORE-001", "P6-GOV-002", "P6-PAPER-001"
    }:
        _fail("field_mismatch", "accepted predecessor set differs")
    predecessor_values = {
        key: _provenance_binding(root, value, f"accepted_predecessors.{key}")
        for key, value in sorted(predecessor.items())
    }

    return P6ForwardProductViewConfig(
        path=path,
        config_sha256=_sha(path),
        strategy_card_sha256=_sha(card_path),
        entry_sha256=_sha(entry_path),
        data_sha256=_sha(data_path),
        score_sha256=_sha(score_path),
        worker_sha256=_sha(worker_path),
        paper_sha256=_sha(paper_path),
        entry=entry,
        data=data,
        score=score,
        worker=worker,
        paper=paper,
        predecessor_acceptance=predecessor_values,
    )


class ForwardValidationProductView:
    def __init__(
        self,
        root: Path,
        store: PlatformStore,
        config_path: Path | None = None,
        candidate_run_root: Path | None = None,
        runtime_path: Path | None = None,
        forward_run_root: Path | None = None,
        worker_revision: str | None = None,
    ) -> None:
        self.root = root.resolve()
        self.store = store
        self.base24_runtime = None
        self.forward_run_root = forward_run_root
        self.worker_revision = worker_revision
        if any(value is not None for value in (runtime_path, forward_run_root, worker_revision)):
            if (
                runtime_path is None
                or forward_run_root is None
                or not forward_run_root.is_absolute()
                or not isinstance(worker_revision, str)
                or re.fullmatch(r"[0-9a-f]{40}", worker_revision) is None
            ):
                _fail(
                    "base24_view_configuration",
                    "runtime, absolute run root and Worker SHA required",
                )
            self.base24_runtime = load_base24_runtime(runtime_path, root=self.root)
        self.candidate_run_root = (
            None if candidate_run_root is None else candidate_run_root.resolve()
        )
        self.config = load_forward_product_view_config(
            config_path
            or self.root / "config/forward-validation/web/p6-forward-product-view.v1.json",
            root=self.root,
        )

    def _activation_decision(self) -> dict[str, object] | None:
        if self.base24_runtime is not None:
            return self._base24_activation_decision()
        if self.candidate_run_root is None:
            return None
        activation_root = (self.candidate_run_root / "p6-forward/activation").resolve()
        pointer_path = activation_root / "CURRENT.json"
        if not pointer_path.is_file():
            return None
        pointer = _load(pointer_path)
        if (
            set(pointer) != {"schema_version", "decision_path", "decision_sha256"}
            or pointer.get("schema_version") != "quanthus.p6-activation-current.v1"
        ):
            _fail("activation_projection_drift", "activation pointer differs")
        relative = Path(str(pointer["decision_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            _fail("path_escape", "activation decision path")
        decision_path = (activation_root / relative).resolve()
        if activation_root not in decision_path.parents or not decision_path.is_file():
            _fail("activation_projection_drift", "activation decision is missing")
        if _sha(decision_path) != _digest(pointer["decision_sha256"], "decision_sha256"):
            _fail("activation_projection_drift", "activation pointer digest differs")
        try:
            runtime = load_prospective_runtime_config(
                self.root
                / "config/forward-validation/activation/p6-prospective-runtime.v1.json",
                root=self.root,
            )
            mandate = load_owner_activation_mandate(
                self.root
                / "config/forward-validation/activation/p6-owner-activation-mandate.v1.json",
                runtime=runtime,
            )
            decision = load_activation_decision(
                decision_path,
                runtime=runtime,
                expected_mandate_digest=mandate.mandate_digest,
            )
        except ForwardValidationContractError as exc:
            _fail("activation_projection_drift", str(exc))
        if not decision.enabled:
            _fail("activation_projection_drift", "activation decision is not enabled")
        return {
            "decision_id": decision.decision_id,
            "decision_at": decision.decision_at.isoformat(),
            "activation_market_date": decision.activation_market_date.isoformat(),
            "decision_digest": decision.decision_digest,
            "deployment_revision": decision.deployment_revision,
        }

    def _base24_activation_decision(self) -> dict[str, object] | None:
        assert self.forward_run_root is not None and self.base24_runtime is not None
        path = self.forward_run_root / "activation/base24-enabled.json"
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            _fail("path_escape", "Base 2.4 activation must use physical paths")
        if not path.is_file():
            return None
        try:
            decision = activation_decision_from_mapping(
                _load(path), runtime=cast(Any, self.base24_runtime), path=path
            )
        except ForwardValidationContractError as exc:
            _fail("activation_projection_drift", str(exc))
        if (
            decision.deployment_revision != self.worker_revision
            or decision.decision_at > datetime.now(timezone.utc)
        ):
            _fail("activation_projection_drift", "Worker revision or activation time differs")
        return {
            "decision_id": decision.decision_id,
            "decision_at": decision.decision_at.isoformat(),
            "activation_market_date": decision.activation_market_date.isoformat(),
            "decision_digest": decision.decision_digest,
            "deployment_revision": decision.deployment_revision,
            "state": decision.state,
        }

    def _base24_jobs(self, activation: Mapping[str, object] | None) -> tuple[JobRecord, ...]:
        assert self.base24_runtime is not None and self.worker_revision is not None
        jobs = self.store.list_jobs(P6_FORWARD_CYCLE_JOB, 200)
        if len(jobs) >= 200:
            _fail("base24_view_job_census_limit", "complete bounded Job census required")
        selected = []
        for job in jobs:
            value = job.input_ref
            if value.get("mode") == "disabled_rehearsal":
                expected = f"production-preactivation-{self.worker_revision[:12]}"
                if value.get("rehearsal_id") == expected:
                    selected.append(job)
            elif (
                activation is not None
                and value.get("runtime_sha256") == self.base24_runtime.config_sha256
                and isinstance(value.get("activation_decision"), Mapping)
                and value["activation_decision"].get("decision_digest")
                == activation["decision_digest"]
            ):
                selected.append(job)
        return tuple(selected)

    def _latest_job(self, jobs: tuple[JobRecord, ...] | None = None) -> dict[str, object] | None:
        if jobs is None:
            jobs = self.store.list_jobs(P6_FORWARD_CYCLE_JOB, 1)
        if not jobs:
            return None
        job = jobs[0]
        summary = None
        if isinstance(job.result_ref, Mapping) and isinstance(
            job.result_ref.get("summary"), Mapping
        ):
            summary = dict(job.result_ref["summary"])
        return {
            "job_id": job.job_id,
            "state": job.state,
            "stage": job.stage,
            "progress": job.progress,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "completed_at": None if job.completed_at is None else job.completed_at.isoformat(),
            "run_id": (
                None
                if not isinstance(job.result_ref, Mapping)
                else job.result_ref.get("run_id")
            ),
            "summary": summary,
        }

    def _official_evidence(
        self, sessions_per_block: int, jobs: tuple[JobRecord, ...] | None = None
    ) -> dict[str, object]:
        official: list[dict[str, Any]] = []
        for job in jobs if jobs is not None else self.store.list_jobs(P6_FORWARD_CYCLE_JOB, 100):
            if (
                job.state != "succeeded"
                or job.input_ref.get("mode") != "official_cycle"
                or not isinstance(job.result_ref, Mapping)
            ):
                continue
            raw = job.result_ref.get("summary")
            if not isinstance(raw, Mapping):
                continue
            summary = dict(raw)
            if (
                summary.get("mode") != "official_cycle"
                or summary.get("official_evidence") is not True
            ):
                continue
            if not isinstance(summary.get("market_date"), str):
                continue
            official.append(summary)
        if not official:
            return {
                "state": "activated_waiting_first_release",
                "official_collection_sessions_completed": 0,
                "independent_blocks_completed": 0,
                "settled_target_count": 0,
                "censored_target_count": 0,
                "first_official_market_date": None,
                "last_admitted_dwd_release": None,
            }
        official.sort(key=lambda value: str(value["market_date"]))
        latest = official[-1]
        collection_count = int(latest.get("collection_session_count", 0))
        tail_count = int(latest.get("tail_session_count", 0))
        if collection_count < 66:
            state = "collecting"
        elif tail_count < 20:
            state = "maturity_tail"
        else:
            state = "budget_complete"
        return {
            "state": state,
            "official_collection_sessions_completed": collection_count,
            "independent_blocks_completed": min(3, collection_count // sessions_per_block),
            "settled_target_count": 0,
            "censored_target_count": 0,
            "first_official_market_date": official[0]["market_date"],
            "last_admitted_dwd_release": latest["market_date"],
        }

    def health_summary(self) -> dict[str, object]:
        observed = self.candidate(_TARGET_STRATEGY, _TARGET_VERSION)
        admission = cast(Mapping[str, Any], observed["admission"])
        evidence = cast(Mapping[str, Any], observed["evidence"])
        return {
            "state": observed["state"],
            "admission_state": admission["state"],
            "official_collection_sessions_completed": evidence[
                "official_collection_sessions_completed"
            ],
            "independent_blocks_completed": evidence["independent_blocks_completed"],
            "first_official_market_date": evidence["first_official_market_date"],
            "last_admitted_dwd_release": evidence["last_admitted_dwd_release"],
        }

    def candidate(self, strategy_id: str, strategy_version: str) -> dict[str, object]:
        if (strategy_id, strategy_version) != (_TARGET_STRATEGY, _TARGET_VERSION):
            return {"state": "not_applicable", "read_only": True}
        entry = self.config.entry
        budget = entry["observation_budget"]
        activation = entry["activation_boundary"]
        paper = self.config.paper
        score = self.config.score
        paper_runtime = paper["execution"]
        roles = score["roles"]
        activation_decision = self._activation_decision()
        admission_state = (
            "disabled"
            if activation_decision is None
            else str(activation_decision.get("state", "enabled"))
        )
        activated = admission_state == "enabled"
        jobs = self._base24_jobs(activation_decision) if self.base24_runtime is not None else None
        source_digests = {
            "web_projection": self.config.config_sha256,
            "strategy_card": self.config.strategy_card_sha256,
            "entry_proposal": self.config.entry_sha256,
            "data_boundary": self.config.data_sha256,
            "scorecard_policy": self.config.score_sha256,
            "forward_worker": self.config.worker_sha256,
            "paper_lane": self.config.paper_sha256,
            **{
                f"acceptance.{key}": value[1]
                for key, value in sorted(self.config.predecessor_acceptance.items())
            },
        }
        if activation_decision is not None:
            source_digests["activation_decision"] = str(activation_decision["decision_digest"])
        if self.base24_runtime is not None:
            source_digests["runtime"] = self.base24_runtime.config_sha256
        evidence = (
            self._official_evidence(int(budget["sessions_per_block"]), jobs)
            if activation_decision is not None
            else {
                "state": "waiting_activation",
                "official_collection_sessions_completed": 0,
                "independent_blocks_completed": 0,
                "settled_target_count": 0,
                "censored_target_count": 0,
                "first_official_market_date": None,
                "last_admitted_dwd_release": None,
            }
        )
        return {
            "state": (
                f"prospective_activation_{admission_state}"
                if activation_decision is not None
                else "engineering_ready_activation_disabled"
            ),
            "read_only": True,
            "strategy_binding": {
                "strategy_id": _TARGET_STRATEGY,
                "strategy_version": _TARGET_VERSION,
                "strategy_semantic_digest": _TARGET_DIGEST,
            },
            "admission": {
                "state": admission_state,
                "activation_owner": activation["activation_owner"],
                "first_official_market_date_rule": activation["first_official_market_date_rule"],
                "required_release_state": activation["required_release_state"],
                "official_prospective_cycle_authorized": activated,
                "activation_decision": activation_decision,
            },
            "evidence": evidence,
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
            "latest_forward_job": self._latest_job(jobs),
            "scorecards": {
                "state": "active_prospective" if activated else "frozen_pre_result",
                "roles": {
                    role: {
                        "source": value["source"],
                        "primary_metrics": list(value["primary_metrics"]),
                        "guardrails": list(value["guardrails"]),
                    }
                    for role, value in sorted(roles.items())
                },
                "material_loss_threshold_cny": str(
                    score["material_loss"]["absolute_threshold_cny"]
                ),
                "observed_scorecards": [],
            },
            "paper": {
                "state": "paper_authority_active" if activated else "engineering_ready_not_active",
                "authority": "paper",
                "actual_execution_authorized": False,
                "execution_profile": paper_runtime["profile_id"],
                "unit_count": paper_runtime["unit_count"],
                "unit_notional_cny": paper_runtime["unit_notional_cny"],
                "configured_total_notional_cny": format(
                    Decimal(str(paper_runtime["unit_notional_cny"]))
                    * int(paper_runtime["unit_count"]),
                    ".2f",
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
                *(
                    []
                    if activation_decision is not None
                    else [
                        "No official prospective observation exists before a separate "
                        "P6-ACTIVATE-001 decision."
                    ]
                ),
                "Pre-activation 2026 history is engineering rehearsal only and cannot "
                "increase budget progress.",
                "Arbitration 2022-2024 and permanent blind 2025 remain sealed.",
                "The current page is read-only and exposes no activation, broker, "
                "notification, live-capital or parameter-edit authority.",
            ],
        }
