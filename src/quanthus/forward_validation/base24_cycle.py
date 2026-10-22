"""Explicit Base 2.4 successor to the frozen legacy P6 source adapter.

P10 supplies a qualified, portable runtime window. This module consumes it; it
does not repair market data or grant activation. The existing paper engine,
candidate rule, two experts, observation budget and immutable Run remain shared.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from quanthus.forward_validation.activation import (
    _ENTRY_DIGEST,
    P6ActivationDecision,
    _digest,
    _object,
    _repo_binding,
    _sha,
    _sha40,
    load_prospective_runtime_config,
)
from quanthus.forward_validation.base24_runtime import (
    CLOCK_VERSION,
    AfterCloseDecisionClock,
    P6Base24CandidatePolicy,
    P6Base24RuntimeInputs,
    open_runtime_inputs,
)
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.data_boundary import (
    DwdVisibilityReceipt,
    P6AccessLedgerEntry,
    P6ActivationBoundary,
    P6ReleaseAdmission,
    ProspectiveDwdRelease,
    classify_release_admission,
)
from quanthus.forward_validation.serialization import canonical_json_bytes, digest_value

RUNTIME_SCHEMA = "quanthus.p6-base24-cycle-runtime.v1"
RELEASE_SCHEMA = "quanthus.p6-base24-qualified-daily.v1"


def access_entry(
    *,
    release: ProspectiveDwdRelease,
    admission: P6ReleaseAdmission,
    decision_at: datetime,
    consumed_at: datetime,
) -> P6AccessLedgerEntry:
    """Successor after-close clock, including an honest after-midnight decision.

    The legacy same-date access check remains unchanged. The clock version and
    complete clock are sealed in this adapter's projection and event records.
    """
    from datetime import timezone

    if not isinstance(release, Base24Release):
        fail("binding_type_mismatch", "after-close access requires qualified Base 2.4 input")
    m = json.loads(release.inputs.manifest_json)
    AfterCloseDecisionClock(
        release.market_date, datetime.fromisoformat(m["source_available_at"]), decision_at
    )
    if consumed_at.tzinfo is None or consumed_at < release.visibility.consumer_visible_at:
        fail("pre_visibility_read", "Base 2.4 consumer receipt")
    if consumed_at != decision_at:
        fail("p6_base24_clock_binding", "cycle input must bind the actual decision instant")
    semantic = {
        "purpose": "p6_base24_after_close_cycle_input",
        "package_id": release.package_id,
        "market_date": release.market_date.isoformat(),
        "manifest_sha256": release.manifest_sha256,
        "content_digest": release.content_digest,
        "decision_knowledge_cutoff": decision_at.astimezone(timezone.utc).isoformat(),
        "consumed_at": consumed_at.astimezone(timezone.utc).isoformat(),
        "entry_proposal_digest": _ENTRY_DIGEST,
        "activation_decision_digest": admission.activation_decision_digest,
        "official_evidence": admission.official_evidence,
        "partition": "prospective_daily_dwd",
    }
    return P6AccessLedgerEntry(
        digest_value(semantic),
        str(semantic["purpose"]),
        release.package_id,
        release.market_date,
        release.manifest_sha256,
        release.content_digest,
        decision_at,
        consumed_at,
        str(semantic["entry_proposal_digest"]),
        admission.activation_decision_digest,
        admission.official_evidence,
        "prospective_daily_dwd",
    )


@dataclass(frozen=True)
class P6Base24CycleRuntime:
    path: Path
    config_sha256: str
    root: Path
    bindings: Mapping[str, Path]
    daily_root: Path


def load_runtime(path: Path, *, root: Path) -> P6Base24CycleRuntime:
    """Reuse frozen *policy* bindings, never the legacy source/activation path."""
    raw = _object(path, "Base 2.4 cycle runtime")
    reference = root / "config/forward-validation/activation/p6-prospective-runtime.v1.json"
    expected = {
        "schema_version": RUNTIME_SCHEMA,
        "work_item": "P6-R1",
        "state": "PRE_ACTIVATION",
        "frozen_policy_reference": {
            "path": str(reference.relative_to(root)),
            "sha256": _sha(reference),
        },
        "qualified_daily": {
            "root_env": "QUANTHUS_P6_BASE24_DAILY_ROOT",
            "default_root": "/data/p6-base24-daily",
            "schema_version": RELEASE_SCHEMA,
            "legacy_equivalence": False,
        },
        "clock_version": CLOCK_VERSION,
        "source_scope": "qualified_action_free_window_with_per_listing_holds",
        "first_official_session": "strictly_after_new_activation_market_date",
        "authority": {
            "official_execution_authorized": False,
            "paper_only": True,
            "notification_authorized": False,
            "broker_authorized": False,
            "live_capital_authorized": False,
            "protected_historical_partitions_authorized": False,
        },
    }
    if raw != expected:
        fail("p6_base24_cycle_runtime_drift", "explicit successor contract differs")
    _repo_binding(root, raw["frozen_policy_reference"], "frozen_policy_reference")
    # This loader checks repository bytes only. None of its legacy data paths is
    # opened or copied to the successor config.
    frozen = load_prospective_runtime_config(reference, root=root)
    daily_root = Path(os.environ.get("QUANTHUS_P6_BASE24_DAILY_ROOT", "/data/p6-base24-daily"))
    if not daily_root.is_absolute():
        fail("path_escape", "Base 2.4 qualified Daily root must be absolute")
    return P6Base24CycleRuntime(
        path.resolve(), _sha(path), root.resolve(), frozen.bindings, daily_root.resolve()
    )


def load_cycle_runtime(path: Path, *, root: Path) -> Any:
    schema = _object(path, "P6 runtime").get("schema_version")
    if schema == RUNTIME_SCHEMA:
        return load_runtime(path, root=root)
    return load_prospective_runtime_config(path, root=root)


@dataclass(frozen=True)
class Base24Release(ProspectiveDwdRelease):
    inputs: P6Base24RuntimeInputs
    envelope_json: str


def _validate_prior(inputs: P6Base24RuntimeInputs, prior: Any) -> None:
    m = json.loads(inputs.manifest_json)
    if not isinstance(prior, dict) or set(prior) != {
        "market_date",
        "package_id",
        "manifest_sha256",
    }:
        fail("p6_base24_release_contract", "exact predecessor required")
    pq = importlib.import_module("pyarrow.parquet")

    days = sorted(
        {
            r["trade_date"]
            for r in pq.read_table(
                inputs.root / "tables/trading_calendar.parquet", columns=["trade_date"]
            ).to_pylist()
        }
    )
    if (
        len(days) < 2
        or days[-1].isoformat() != m["market_date"]
        or (days[-2].isoformat() != prior["market_date"])
    ):
        fail("official_cycle_session_gap", "predecessor must be the actual prior session")
    if (prior["package_id"] is None) != (prior["manifest_sha256"] is None):
        fail("p6_base24_release_contract", "predecessor identity is incomplete")
    if prior["manifest_sha256"] is not None:
        _digest(prior["manifest_sha256"], "prior manifest")
        if not isinstance(prior["package_id"], str) or not prior["package_id"]:
            fail("p6_base24_release_contract", "prior package id")


def seal_release(
    *,
    inputs: P6Base24RuntimeInputs,
    output: Path,
    package_id: str,
    code_revision: str,
    prior: Mapping[str, Any],
) -> dict[str, Any]:
    """Package admitted P10 inputs; never repair or relabel their business date.

    This is an input publication, not activation or an official observation.
    Failed attempts remain without READY; a sealed output is never overwritten.
    """
    from datetime import timezone

    from quanthus.pipelines.securities_warehouse.base22_sources import disjoint_output

    _sha40(code_revision, "code_revision")
    if not package_id or Path(package_id).name != package_id or package_id in {".", ".."}:
        fail("path_escape", "qualified Daily package id")
    inputs.check_unchanged()
    m = json.loads(inputs.manifest_json)
    _validate_prior(inputs, dict(prior))
    output = disjoint_output(output, [inputs.root])
    if output.exists():
        fail("p6_base24_output_exists", "qualified Daily publication already exists")
    output.mkdir(parents=True)
    shutil.copytree(inputs.root, output / "input")
    inputs.check_unchanged()
    semantic = {
        "schema_version": RELEASE_SCHEMA,
        "package_id": package_id,
        "market_date": m["market_date"],
        "code_revision": code_revision,
        "input_manifest_sha256": inputs.manifest_sha256,
        "prior": dict(prior),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = {**semantic, "content_digest": digest_value(semantic)}
    (output / "manifest.json").write_bytes(canonical_json_bytes(payload) + b"\n")
    # The inner copy's READY and all hashes are checked before final publication.
    open_runtime_inputs(output / "input", manifest_sha256=inputs.manifest_sha256)
    (output / "READY").write_text(_sha(output / "manifest.json") + "\n", encoding="ascii")
    return {"manifest_sha256": _sha(output / "manifest.json"), **payload}


def frame_bundle(
    inputs: P6Base24RuntimeInputs, clock: AfterCloseDecisionClock | None, root: Path
) -> Any:
    """Complete the frame reader's empty action schemas, without inventing rows.

    Runtime input admission already proves this retained scope is action-free.
    Keep its original bytes sealed; only two zero-row consumer tables are derived.
    All other physical files are referenced once inside this same sealed Job.
    """
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")

    bundle = inputs.context_bundle() if clock is None else inputs.bundle(clock)
    relative_input = inputs.root.relative_to(root)
    additions = {
        "corporate_actions_normalized": {
            "action_id": pa.string(),
            "source_revision": pa.string(),
            "source_snapshot_id": pa.string(),
            "evidence_digest": pa.string(),
            "execution_authorized": pa.bool_(),
        },
        "corporate_action_exceptions": {
            "exception_id": pa.string(),
            "reason_code": pa.string(),
            "replay_disposition": pa.string(),
            "execution_authorized": pa.bool_(),
        },
    }
    (root / "frame").mkdir()
    datasets = []
    for dataset in bundle.datasets:
        name = dataset.dataset
        if name in additions:
            table = pq.read_table(inputs.root / dataset.partitions[0])
            if table.num_rows != 0:
                fail("p6_base24_action_scope_drift", "empty header adaptation requires no actions")
            for column, kind in additions[name].items():
                if column not in table.column_names:
                    table = table.append_column(column, pa.array([], type=kind))
            relative = f"frame/{name}.parquet"
            pq.write_table(table, root / relative)
            datasets.append(
                replace(
                    dataset,
                    partitions=(relative,),
                    schema_hash=digest_value(str(table.schema)),
                    content_checksums={relative: _sha(root / relative)},
                    source_revisions={**dataset.source_revisions, "frame_adapter": RUNTIME_SCHEMA},
                )
            )
        else:
            paths = tuple(str(relative_input / part) for part in dataset.partitions)
            datasets.append(
                replace(
                    dataset,
                    partitions=paths,
                    content_checksums={
                        str(relative_input / key): value
                        for key, value in dataset.content_checksums.items()
                    },
                )
            )
    inputs.check_unchanged()
    return replace(bundle, root=root, datasets=tuple(datasets))


def verify_release(root: Path, visibility: DwdVisibilityReceipt) -> Base24Release:
    """Deep verify the self-contained handoff, without old Base/Daily roots."""
    from quanthus.pipelines.securities_warehouse.base22_sources import _tree, physical_path

    root = physical_path(root)
    manifest = root / "manifest.json"
    sha = _sha(manifest)
    if (root / "READY").read_text(encoding="ascii").strip() != sha:
        fail("binding_digest_drift", "Base 2.4 Daily READY")
    m = _object(manifest, "Base 2.4 qualified Daily")
    keys = {
        "schema_version",
        "package_id",
        "market_date",
        "code_revision",
        "input_manifest_sha256",
        "built_at",
        "prior",
        "content_digest",
    }
    if set(m) != keys or m["schema_version"] != RELEASE_SCHEMA:
        fail("p6_base24_release_contract", "qualified Daily envelope")
    _sha40(m["code_revision"], "code_revision")
    semantic = {k: v for k, v in m.items() if k != "content_digest"}
    if digest_value(semantic) != m["content_digest"]:
        fail("binding_digest_drift", "Base 2.4 Daily content digest")
    if (
        m["package_id"] != visibility.package_id
        or m["market_date"] != visibility.market_date.isoformat()
        or sha != visibility.manifest_sha256
        or m["content_digest"] != visibility.content_digest
        or not m["package_id"]
        or Path(m["package_id"]).name != m["package_id"]
    ):
        fail("binding_digest_drift", "qualified Daily visibility")
    inputs = open_runtime_inputs(
        root / "input", manifest_sha256=_digest(m["input_manifest_sha256"], "input manifest")
    )
    inner = json.loads(inputs.manifest_json)
    if inner["market_date"] != m["market_date"]:
        fail("p6_base24_release_contract", "inner/outer target date")
    available = datetime.fromisoformat(inner["source_available_at"])
    built = datetime.fromisoformat(m["built_at"])
    if built.tzinfo is None or built < available or visibility.ready_observed_at < built:
        fail("p6_base24_not_yet_visible", "receipt predates actual input availability")
    expected_files = {"manifest.json", "READY"} | {"input/" + name for name in inputs.fingerprints}
    if _tree(root) != expected_files:
        fail("p6_base24_release_contract", "exact physical envelope required")
    _validate_prior(inputs, m["prior"])
    inputs.check_unchanged()
    return Base24Release(
        root,
        visibility.package_id,
        visibility.market_date,
        sha,
        visibility.content_digest,
        m["code_revision"],
        MappingProxyType({k: v["rows"] for k, v in inner["files"].items()}),
        sum(v["bytes"] for v in inner["files"].values()),
        visibility,
        inputs,
        json.dumps(m, sort_keys=True),
    )


def build_projection(
    *,
    config: P6Base24CycleRuntime,
    activation: P6ActivationDecision,
    target: Base24Release,
    output_root: Path,
    decision_at: datetime,
    collect_candidates: bool = True,
) -> Any:
    """One immutable in-Job input and feature panel for the unchanged P6 engine."""
    from datetime import timedelta

    pq = importlib.import_module("pyarrow.parquet")

    from quanthus.backtest.baseline_research import BaselineFeaturePanel
    from quanthus.event_library.rule_card import load_candidate_rule_card
    from quanthus.event_library.scan import scan_forward_candidate_series
    from quanthus.feature_registry import load_feature_registry
    from quanthus.forward_validation.prospective_source import (
        P6ProspectiveCandidate,
        P6ProspectiveProjection,
        P6ProspectiveSplitPolicy,
    )
    from quanthus.industry_mapping import load_static_industry_mapping
    from quanthus.pipelines.securities_warehouse.base22_sources import disjoint_output

    boundary = P6ActivationBoundary(
        activation.decision_digest,
        activation.decision_at,
        activation.activation_market_date,
        activation.enabled,
    )
    admission = classify_release_admission(target, boundary)
    if not admission.official_evidence:
        fail("official_cycle_not_authorized", admission.reason_code)
    target.inputs.check_unchanged()
    inner = json.loads(target.inputs.manifest_json)
    clock = AfterCloseDecisionClock(
        target.market_date, datetime.fromisoformat(inner["source_available_at"]), decision_at
    )
    if decision_at < target.visibility.consumer_visible_at:
        fail("p6_base24_not_yet_visible", "actual consumer receipt follows decision")
    root = disjoint_output(output_root, [target.package_root])
    if root.exists():
        fail("p6_base24_output_exists", "projection already exists")
    root.mkdir(parents=True)
    shutil.copytree(target.inputs.root, root / "effective-view")
    target.inputs.check_unchanged()
    inputs = open_runtime_inputs(
        root / "effective-view", manifest_sha256=target.inputs.manifest_sha256
    )
    bundle = frame_bundle(inputs, clock, root)
    feature_path = root / "feature-panel.parquet"
    feature = BaselineFeaturePanel.materialize(
        bundle,
        load_feature_registry(config.bindings["feature_registry"]),
        scope_start=date.fromisoformat(inner["window_start"]),
        scope_end=target.market_date,
        output=feature_path,
        data_content_digest=bundle.digest,
        data_manifest_sha256=target.manifest_sha256,
        output_order="security-date",
    )
    candidate_path = root / "candidate-daily.parquet"
    shutil.copyfile(inputs.root / "candidate-input.parquet", candidate_path)
    dates = tuple(
        sorted(
            {
                r["trade_date"]
                for r in pq.read_table(
                    inputs.root / "tables/trading_calendar.parquet", columns=["trade_date"]
                ).to_pylist()
            }
        )
    )
    policy = P6Base24CandidatePolicy(
        clock, date.fromisoformat(inner["window_start"]), inputs.manifest_sha256
    )
    split = P6ProspectiveSplitPolicy(activation.activation_market_date, target.market_date)
    scan = None
    if collect_candidates:
        scan = scan_forward_candidate_series(
            inputs.candidate_series(clock),
            trading_dates=(*dates, target.market_date + timedelta(days=1)),
            snapshot_id=bundle.snapshot.snapshot_id,
            snapshot_manifest_digest=target.manifest_sha256,
            card=load_candidate_rule_card(config.bindings["candidate_rule"]),
            # The scanner still annotates its frozen historical concrete types;
            # these are its existing prospective structural interfaces.
            split_policy=cast(Any, split),
            source_policy=cast(Any, policy),
        )
    candidates = tuple(
        P6ProspectiveCandidate(
            item.record,
            item.listing_id,
            item.listing_code,
            item.company_id,
            item.static_level1_industry_id,
            item.feature_start,
            MappingProxyType(item.feature_evidence.to_dict()),
        )
        for item in (() if scan is None else scan.candidates)
    )
    counters = (
        {
            "evaluated_rows": 0,
            "raw_triggers": 0,
            "isolated_candidates": 0,
            "concentration_retained": 0,
            "candidate_events": 0,
            "reason_counts": {},
        }
        if scan is None
        else scan.counters.to_dict()
    )
    candidate_set = {
        "schema_version": "quanthus.p6-prospective-candidate-set.v1",
        "signal_market_date": target.market_date.isoformat(),
        "entry_state": "pending_next_ready_session",
        "scanner_internal_t_plus_1_sentinel_persisted": False,
        "counters": counters,
        "candidates": [c.to_dict() for c in candidates],
    }
    candidate_set_path = root / "candidate-events.json"
    candidate_set_path.write_bytes(canonical_json_bytes(candidate_set) + b"\n")
    prior = json.loads(target.envelope_json)["prior"]
    manifest = {
        "schema_version": "quanthus.p6-prospective-projection.v1",
        "source_adapter": RUNTIME_SCHEMA,
        "runtime_sha256": config.config_sha256,
        "activation_decision_digest": activation.decision_digest,
        "target_release_identity": target.identity_digest,
        "target_manifest_sha256": target.manifest_sha256,
        "input_manifest_sha256": inputs.manifest_sha256,
        "history_start": inner["window_start"],
        "signal_market_date": target.market_date.isoformat(),
        "decision_clock": clock.to_dict(),
        "direct_prior": prior,
        "candidate_scan": {
            "collection_enabled": collect_candidates,
            **counters,
            "event_ids": [c.record.event_id for c in candidates],
            "entry_state": "pending_next_ready_session",
            "scanner_internal_t_plus_1_sentinel_persisted": False,
        },
        "feature_panel": feature.report.payload_dict(),
        "files": {
            "candidate_daily": _sha(candidate_path),
            "candidate_events": _sha(candidate_set_path),
            "feature_panel": _sha(feature_path),
            "effective_view_digest": bundle.digest,
        },
        "sealed_historical_partition_reads": 0,
    }
    manifest["content_digest"] = digest_value(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    (root / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
    inputs.check_unchanged()
    industry = load_static_industry_mapping(
        inputs.root / "industry.json", expected_version="static-level1-concentration-research-v1"
    )
    return P6ProspectiveProjection(
        root,
        candidate_path,
        candidate_set_path,
        feature_path,
        manifest_path,
        _sha(manifest_path),
        manifest["content_digest"],
        bundle.snapshot.snapshot_id,
        target.manifest_sha256,
        dates,
        target.market_date,
        date.fromisoformat(prior["market_date"]),
        prior["package_id"],
        prior["manifest_sha256"],
        date.fromisoformat(inner["window_start"]),
        bundle,
        feature.report,
        industry,
        cast(Any, policy),
        split,
    )
