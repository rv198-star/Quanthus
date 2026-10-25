"""Completed archive checks use synthetic money and never warehouse fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_history_attribution as attribution
from quanthus.expert_strategy import p3_history_review as review

D = Decimal


def event(kind, at, payload, sequence):
    return {
        "event_id": f"event-{sequence}",
        "event_type": kind,
        "event_time": at,
        "payload": payload,
        "sequence": sequence,
        "reason_code": "synthetic",
    }


@pytest.fixture
def example():
    digest = "sha256:" + "b" * 64
    coordinates = {"entry:synthetic": "000001@2012-01-04"}
    contract = review.ArchiveContract(
        purpose="synthetic-history",
        request_digest="sha256:" + "1" * 64,
        execution_digest="sha256:" + "2" * 64,
        source_manifest_sha256="sha256:" + "3" * 64,
        source_information_cutoff="2026-10-25T00:00:00+00:00",
        optional_rights_policy_sha256="sha256:" + "4" * 64,
        scope={
            "decision_start": "2012-01-04",
            "decision_end": "2012-01-05",
            "execution_end": "2012-01-06",
        },
        arms=("B", "T", "TCH"),
        initial_unit_capital="1000",
        unit_ids=("unit-1",),
        sessions=2,
        completion_sha256={},
        coordinate_sha256={},
    )
    decision = {
        "surface": "entry",
        "decision_key": "entry:synthetic",
        "market_date": "2012-01-04",
        "unit_id": "unit-1",
        "filled": True,
        "allocated": True,
        "final_action": "trial_entry",
        "aggregate_score": "1",
        "reason_codes": ["entry_score_threshold_met"],
        "formal_decision_digest": digest,
    }
    events = [
        event(
            "signal_accepted",
            "2012-01-04T15:00:00+08:00",
            {
                "signal_id": "signal-buy",
                "decision_version": "v1|" + digest,
                "decision_reason_code": "formal_entry",
                "side": "buy",
            },
            1,
        )
    ]
    for index, (side, day, price) in enumerate(
        [("buy", "2012-01-05", "10"), ("sell", "2012-01-06", "20")]
    ):
        order = "order-" + side
        events.append(
            event(
                "order_created",
                day + "T09:00:00+08:00",
                {
                    "order_id": order,
                    "signal_id": "signal-" + side,
                    "unit_id": "unit-1",
                    "security_code": "000001",
                    "side": side,
                },
                index * 3 + 2,
            )
        )
        events.append(
            event(
                "fill",
                day + "T09:30:00+08:00",
                {
                    "order_id": order,
                    "fill_id": "fill-" + side,
                    "side": side,
                    "filled_at": day + "T09:30:00+08:00",
                    "price": price,
                    "quantity": 10,
                    "gross_amount": str(D(price) * 10),
                    "total_fees": "2",
                    "commission": "2",
                    "stamp_duty": "0",
                    "transfer_fee": "0",
                },
                index * 3 + 3,
            )
        )
        events.append(
            event(
                "daily_snapshot",
                day + "T15:00:00+08:00",
                {
                    "unit_id": "unit-1",
                    "security_code": "000001",
                    "session_date": day,
                    "as_of": day + "T15:00:00+08:00",
                    "cash": "898" if side == "buy" else "1096",
                    "market_value": "100" if side == "buy" else "0",
                    "external_pool": "0",
                    "position_quantity": 10 if side == "buy" else 0,
                    "dividend_income": "0",
                },
                index * 3 + 4,
            )
        )
    equity = [
        {
            "market_date": "2012-01-04",
            "system_equity": "1000",
            "market_value": "0",
            "active_unit_count": 0,
            "invested_capital_ratio": "0",
        },
        {
            "market_date": "2012-01-05",
            "system_equity": "998",
            "market_value": "100",
            "active_unit_count": 1,
            "invested_capital_ratio": str((D(100) / 998).quantize(review.RATE, ROUND_HALF_UP)),
        },
        {
            "market_date": "2012-01-06",
            "system_equity": "1096",
            "market_value": "0",
            "active_unit_count": 0,
            "invested_capital_ratio": "0",
        },
    ]
    data = {
        "decisions.json": [decision],
        "engine-events.json": events,
        "equity.json": equity,
        "daily-returns.json": [
            {
                "market_date": equity[i]["market_date"],
                "return": str(
                    (
                        D(equity[i]["system_equity"]) / D(equity[i - 1]["system_equity"]) - 1
                    ).quantize(review.RATE, ROUND_HALF_UP)
                ),
            }
            for i in range(1, 3)
        ],
        "final-ledgers.json": [
            {"commission_expense": "4", "stamp_duty_expense": "0", "transfer_fee_expense": "0"}
        ],
        "reconciliation.json": {"reconciled": True, "sealed_partition_access": False},
    }
    return data, coordinates, contract


def test_independent_actual_cashflow_and_rounded_returns_reconcile(example):
    data, coordinates, contract = example
    result = review.validate_bundle(data, coordinates, contract)
    assert result["net_pnl"] == "96"
    assert result["position_cycles"] == 1
    assert result["accounting_residual"] == "0"
    assert result["fees"] == "4"


@pytest.mark.parametrize(
    "change,reason",
    [
        ("fee", "FILL_FEES"),
        ("gross", "FILL_GROSS"),
        ("return", "RETURN"),
        ("date", "EVENT_DATE"),
        ("coordinate", "COORDINATE_DATE"),
        ("snap", "DAILY_ACCOUNTING"),
        ("timezone", "UNKNOWN_TIMEZONE"),
        ("protected", "PROTECTED_PARTITION"),
        ("final_fee", "FINAL_FEES"),
    ],
)
def test_semantic_damage_is_refused_without_relying_on_a_file_hash(example, change, reason):
    data, coordinates, contract = example
    if change == "fee":
        data["engine-events.json"][2]["payload"]["total_fees"] = "3"
    elif change == "gross":
        data["engine-events.json"][2]["payload"]["gross_amount"] = "101"
    elif change == "return":
        data["daily-returns.json"][0]["return"] = "0"
    elif change == "date":
        data["engine-events.json"][0]["event_time"] = "2012-02-01T15:00:00+08:00"
    elif change == "coordinate":
        coordinates["entry:synthetic"] = "000001@2012-01-05"
    elif change == "snap":
        data["engine-events.json"][3]["payload"]["cash"] = "897"
    elif change == "timezone":
        data["engine-events.json"][0]["event_time"] = "2012-01-04T15:00:00"
    elif change == "protected":
        data["reconciliation.json"]["sealed_partition_access"] = True
    elif change == "final_fee":
        data["final-ledgers.json"][0]["commission_expense"] = "3"
    with pytest.raises(ValueError, match=reason):
        review.validate_bundle(data, coordinates, contract)


def test_attribution_partition_is_actual_not_counterfactual(example):
    data, coordinates, contract = example
    summary = review.validate_bundle(data, coordinates, contract)
    study = review.ReviewedStudy(
        primary={arm: copy.deepcopy(data) for arm in contract.arms},
        coordinates=coordinates,
        summaries={arm: summary for arm in contract.arms},
        proof={},
    )
    result = attribution.attribute(study, contract)
    assert result["no_counterfactual_profit_imputed"]
    assert all(v["net_pnl_delta"] == "0" for v in result["contrasts"].values())
    assert result["contrasts"]["T-B"]["shared_cycle_count"] == 1
    assert result["variants"]["B"]["calendar_periods"]["2012"]["net_pnl"] == "96"


@pytest.fixture
def control_archives(example, tmp_path, monkeypatch):
    from quanthus.expert_strategy import p3_historical_controls as controls

    data, coordinates, contract = example
    monkeypatch.setattr(controls, "business_bundle", lambda artifacts, *_a, **_k: artifacts)
    contract = replace(
        contract,
        completion_sha256={role: "unused-parent-receipt" for role in review.ROLES},
        coordinate_sha256={role: "unused-parent-receipt" for role in review.ROLES},
    )
    control_digest = "sha256:" + "c" * 64
    freeze = {
        "parent_archive_contract": contract.__dict__,
        "arm": "TC",
        "candidate_keys": 1,
        "control_request_digest": control_digest,
        "roles": {},
    }
    for role in review.ROLES:
        root = tmp_path / role
        root.mkdir()
        folder = root / "replays/variant.TC"
        folder.mkdir(parents=True)
        write_json(root / "event-coordinates.json", coordinates)
        hashes = {name: write_json(folder / name, value) for name, value in data.items()}
        digest = digest_value({name: digest_value(v) for name, v in sorted(data.items())})
        manifest = {
            "purpose": controls.VERSION,
            "execution_adapter": controls.VERSION,
            "variant_id": "variant.TC",
            "variant": "TC",
            "scope": contract.scope,
            "source_manifest_sha256": contract.source_manifest_sha256,
            "source_information_cutoff": contract.source_information_cutoff,
            "execution_digest": control_digest,
            "parent_request_digest": contract.request_digest,
            "input_snapshot_namespace": contract.purpose + "/" + contract.request_digest[7:],
            "optional_rights_policy_sha256": contract.optional_rights_policy_sha256,
            "optional_rights_successor_policy_digest": control_digest,
            "components": controls.COMPONENTS["TC"],
            "no_subscription_all_arms": True,
            "historical_availability_claim": False,
            "execution_authorized": False,
            "production_authorized": False,
            "independently_rebuilt_this_job": False,
            "strict_B_D_strategy_or_production_accepted": False,
            "replay_digest": digest,
        }
        hashes["manifest.json"] = write_json(folder / "manifest.json", manifest)
        source = root / "synthetic-input.txt"
        source.write_text("safe synthetic input")
        bindings = {
            str(source): {
                "sha256": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
                "bytes": source.stat().st_size,
            }
        }
        proof = controls.verify_bindings(bindings)
        write_json(
            root / "replays/COMPLETE.json",
            {
                "status": "COMPLETE",
                "role": role,
                "variant": "TC",
                "control_request_digest": control_digest,
                "source_manifest_sha256": contract.source_manifest_sha256,
                "production_authorized": False,
                "input_physical_proof": proof,
                "file_sha256": hashes,
                "replay_digest": digest,
                "elapsed_seconds": 1,
                "peak_RSS_bytes": 1000,
            },
        )
        write_json(
            root / "isolation.json",
            {
                "kernel_landlock": True,
                "seccomp_network_denied": True,
                "legacy_provider_disabled": True,
                "native_denied": ["other-role-synthetic"],
            },
        )
        freeze["roles"][role] = {
            "work": str(root),
            "bindings": bindings,
            "forbidden": ["other-role-synthetic"],
        }
    return controls, freeze


def test_fixed_control_reuses_own_inputs_but_reproduces_full_business_graph(control_archives):
    controls, freeze = control_archives
    result = controls.review_controls(freeze)
    assert result.proof["business_graph_equal"] is True
    assert result.proof["independently_rebuilt_prerequisites_this_job"] is False
    assert result.summaries["TC"]["net_pnl"] == "96"


def test_control_archive_survives_unreachable_old_input_without_new_source_claim(control_archives):
    controls, freeze = control_archives
    accepted = controls.review_controls(freeze)
    anchor = controls.archive_anchor(freeze, accepted)
    for record in freeze["roles"].values():
        source = Path(next(iter(record["bindings"])))
        source.rename(source.with_suffix(".retained"))
    result = controls.review_control_archive(freeze, anchor)
    assert result.summaries == accepted.summaries
    assert result.proof["business_graph_equal"] is True
    assert result.proof["source_inputs_unchanged"] is False
    assert result.proof["verification_mode"] == "TRUSTED_ARCHIVE_ONLY"


def test_control_archive_can_move_without_rewriting_frozen_identity(control_archives, tmp_path):
    controls, freeze = control_archives
    accepted = controls.review_controls(freeze)
    anchor = controls.archive_anchor(freeze, accepted)
    roots = {}
    for role, record in freeze["roles"].items():
        old = Path(record["work"])
        target = tmp_path / ("moved-" + role)
        shutil.copytree(old, target)
        old.rename(old.with_name("unreachable-" + role))
        roots[role] = str(target)
    assert (
        controls.review_control_archive(freeze, anchor, roots=roots).summaries == accepted.summaries
    )


def test_bh_synthetic_full_business_pack_acceptance(control_archives):
    from quanthus.expert_strategy import p3_benchmark_held as bh

    controls, freeze = control_archives
    freeze["arm"] = "BH"
    for record in freeze["roles"].values():
        root = Path(record["work"])
        folder = root / "replays/variant.TC"
        folder.rename(root / "replays/variant.BH")
        folder = root / "replays/variant.BH"
        manifest = json.loads((folder / "manifest.json").read_bytes())
        manifest.update(
            purpose=bh.VERSION,
            execution_adapter=bh.VERSION,
            variant_id="variant.BH",
            variant="BH",
            components=bh.COMPONENTS["BH"],
        )
        complete = json.loads((root / "replays/COMPLETE.json").read_bytes())
        complete["variant"] = "BH"
        complete["file_sha256"]["manifest.json"] = write_json(folder / "manifest.json", manifest)
        write_json(root / "replays/COMPLETE.json", complete)
    accepted = bh.review_benchmark_held(freeze)
    assert accepted.summaries["BH"]["net_pnl"] == "96"
    anchor = controls.archive_anchor(freeze, accepted)
    assert bh.review_benchmark_held(freeze, anchor).summaries == accepted.summaries


@pytest.mark.parametrize("damage", ["repack", "freeze", "anchor", "missing"])
def test_control_archive_is_pinned_to_external_acceptance_not_self_report(control_archives, damage):
    controls, freeze = control_archives
    anchor = controls.archive_anchor(freeze, controls.review_controls(freeze))
    root = Path(freeze["roles"]["primary"]["work"])
    if damage == "repack":
        complete = json.loads((root / "replays/COMPLETE.json").read_bytes())
        complete["source_manifest_sha256"] = "sha256:" + "0" * 64
        write_json(root / "replays/COMPLETE.json", complete)
    elif damage == "freeze":
        freeze["candidate_keys"] = 2
    elif damage == "anchor":
        anchor["source_inputs_unchanged_at_acceptance"] = False
    else:
        (root / "replays/variant.TC/equity.json").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        controls.review_control_archive(freeze, anchor)


@pytest.mark.parametrize("mutation", ["source", "component", "PIT", "extra", "input", "isolation"])
def test_fully_repacked_control_cannot_hide_semantic_or_physical_damage(control_archives, mutation):
    controls, freeze = control_archives
    record = freeze["roles"]["independent_cold_reproduction"]
    root = Path(record["work"])
    folder = root / "replays/variant.TC"
    if mutation in {"source", "component", "PIT"}:
        manifest = json.loads((folder / "manifest.json").read_bytes())
        if mutation == "source":
            manifest["source_manifest_sha256"] = "sha256:" + "f" * 64
        elif mutation == "component":
            manifest["components"] = {"entry_cap": True, "held_risk": True}
        else:
            manifest["historical_availability_claim"] = True
        digest = write_json(folder / "manifest.json", manifest)
        complete = json.loads((root / "replays/COMPLETE.json").read_bytes())
        complete["file_sha256"]["manifest.json"] = digest
        write_json(root / "replays/COMPLETE.json", complete)
    elif mutation == "extra":
        (folder / "unexpected.json").write_text("{}")
    elif mutation == "input":
        Path(next(iter(record["bindings"]))).write_text("changed synthetic input")
    else:
        isolation = json.loads((root / "isolation.json").read_bytes())
        isolation["native_denied"] = []
        write_json(root / "isolation.json", isolation)
    with pytest.raises(ValueError, match="CONTROL_"):
        controls.review_controls(freeze)


def write_json(path, value):
    path.write_text(json.dumps(value))
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def package(root, data, coordinates, contract, role):
    root.mkdir()
    coordinate_sha = write_json(root / "event-coordinates.json", coordinates)
    replay_root = root / "replays"
    replay_root.mkdir()
    variants = {}
    for arm in contract.arms:
        folder = replay_root / ("variant." + arm)
        folder.mkdir()
        hashes = {name: write_json(folder / name, value) for name, value in data.items()}
        digest = digest_value({name: digest_value(value) for name, value in sorted(data.items())})
        manifest = {
            "purpose": contract.purpose,
            "scope": contract.scope,
            "source_manifest_sha256": contract.source_manifest_sha256,
            "source_information_cutoff": contract.source_information_cutoff,
            "execution_digest": contract.execution_digest,
            "execution_adapter": contract.purpose,
            "optional_rights_policy_sha256": contract.optional_rights_policy_sha256,
            "no_subscription_all_arms": True,
            "historical_availability_claim": False,
            "execution_authorized": False,
            "production_authorized": False,
            "replay_digest": digest,
        }
        hashes["manifest.json"] = write_json(folder / "manifest.json", manifest)
        variants[arm] = {"file_sha256": hashes, "replay_digest": digest}
    complete = {
        "status": "COMPLETE",
        "role": role,
        "kind": contract.purpose,
        "request_digest": contract.request_digest,
        "execution_digest": contract.execution_digest,
        "execution_adapter": contract.purpose,
        "source_manifest_sha256": contract.source_manifest_sha256,
        "production_authorized": False,
        "variants": variants,
    }
    return write_json(replay_root / "COMPLETE.json", complete), coordinate_sha


@pytest.fixture
def archives(example, tmp_path, monkeypatch):
    data, coordinates, contract = example
    # Physical-archive tests isolate transport/closure checks. Real graph adapter is
    # separately tested and is exercised unmocked on the complete existing study.
    monkeypatch.setattr(
        review,
        "business_bundle",
        lambda artifacts, *_a, **_k: {
            name: digest_value(value) for name, value in artifacts.items()
        },
    )
    primary, cold = tmp_path / "primary", tmp_path / "cold"
    completion, coordinate = {}, {}
    for role, root in zip(review.ROLES, (primary, cold), strict=True):
        completion[role], coordinate[role] = package(root, data, coordinates, contract, role)
    contract = replace(contract, completion_sha256=completion, coordinate_sha256=coordinate)
    return primary, cold, contract


def test_archive_verification_needs_only_two_archives_and_explicit_contract(archives):
    primary, cold, contract = archives
    result = review.review_pair(primary, cold, contract)
    assert result.proof["distinct_physical_files"] == 42
    assert result.proof["source_roots_opened"] is False
    assert result.proof["consumer_admission_granted"] is False


@pytest.mark.parametrize("change", ["missing", "bytes", "extra", "symlink", "hardlink"])
def test_physical_damage_and_aliases_fail_closed(archives, change):
    primary, cold, contract = archives
    target = cold / "replays/variant.B/equity.json"
    if change == "missing":
        target.unlink()
    elif change == "bytes":
        target.write_bytes(target.read_bytes() + b" ")
    elif change == "extra":
        (target.parent / "extra.json").write_text("{}")
    elif change == "symlink":
        target.unlink()
        target.symlink_to(primary / "replays/variant.B/equity.json")
    elif change == "hardlink":
        target.unlink()
        os.link(primary / "replays/variant.B/equity.json", target)
    with pytest.raises((ValueError, FileNotFoundError)):
        review.review_pair(primary, cold, contract)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_manifest_sha256", "sha256:" + "f" * 64),
        ("purpose", "unqualified-new-purpose"),
        ("execution_authorized", True),
        ("source_information_cutoff", "2012-01-04T00:00:00+00:00"),
    ],
)
def test_repacked_wrong_source_or_authority_still_fails(archives, field, value):
    primary, cold, contract = archives
    manifest_path = primary / "replays/variant.B/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    digest = write_json(manifest_path, manifest)
    completion_path = primary / "replays/COMPLETE.json"
    completion = json.loads(completion_path.read_text())
    completion["variants"]["B"]["file_sha256"]["manifest.json"] = digest
    completion_hash = write_json(completion_path, completion)
    contract = replace(
        contract, completion_sha256={**contract.completion_sha256, "primary": completion_hash}
    )
    with pytest.raises(ValueError, match="MANIFEST_BINDING"):
        review.review_pair(primary, cold, contract)


def test_old_access_time_change_is_not_a_content_mutation(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text('{"ok": true}')
    os.utime(path, (1, 1))
    value, digest = review.read_json(path)
    assert value == {"ok": True}
    assert digest.startswith("sha256:")


def test_future_arrival_can_change_offline_five_session_sample():
    from datetime import date
    from types import SimpleNamespace

    from quanthus.event_library.rule_card import load_candidate_rule_card
    from quanthus.event_library.scan import _rank_digest, _retain_raw_candidates

    card = load_candidate_rule_card(
        Path(__file__).resolve().parents[2]
        / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    days = [date(2012, 1, d) for d in (4, 5, 6, 9, 10)]
    index = {day: i for i, day in enumerate(days)}

    def candidate(n, day):
        sid = f"{n:06d}"
        return SimpleNamespace(
            security_id=sid,
            signal_date=day,
            industry_id="synthetic",
            rank_digest=_rank_digest(
                rule_digest=card.semantic_digest,
                snapshot_id="synthetic-prefix",
                signal_date=day,
                security_id=sid,
            ),
        )

    early = [candidate(n, days[0]) for n in range(1, 51)]
    late = next(
        candidate(n, days[-1])
        for n in range(900000, 900100)
        if candidate(n, days[-1]).rank_digest < max(r.rank_digest for r in early)
    )
    before = _retain_raw_candidates(early, reasons=Counter(), date_index=index, card=card)[0]
    after = _retain_raw_candidates(early + [late], reasons=Counter(), date_index=index, card=card)[
        0
    ]
    assert len(before) == len(after) == 50
    assert len({r.security_id for r in before} - {r.security_id for r in after}) == 1


def test_original_admission_preserves_expired_clock_and_exact_cap(example):
    _, _, contract = example
    freeze = {
        "UTC": "2026-10-25T09:00:00+00:00",
        "deadline_UTC": "2026-10-25T10:00:00+00:00",
        "request_digest": contract.request_digest,
        "execution_digest": contract.execution_digest,
        "maximum_all_role_artifacts_bytes": 128,
    }
    acceptance = {
        "UTC": "2026-10-25T09:50:00+00:00",
        "status": "PASS",
        "purpose": contract.purpose,
        "source_manifest_sha256": contract.source_manifest_sha256,
        "request_digest": contract.request_digest,
        "execution_digest": contract.execution_digest,
    }
    amendment = {
        "previous_cap_bytes": 128,
        "maximum_all_role_artifacts_bytes": 160,
        "only_existing_six_completed_runs": True,
        "no_replay_source_collection_or_science_change": True,
        "all_other_limits_and_failures_unchanged": True,
        "original_execution_deadline_UTC": freeze["deadline_UTC"],
    }
    result = review.verify_original_admission(freeze, acceptance, amendment, contract, 141)
    assert result["new_replay_authorized"] is False
    with pytest.raises(ValueError, match="ARTIFACT_CAP"):
        review.verify_original_admission(freeze, acceptance, amendment, contract, 161)
    acceptance["UTC"] = "2026-10-25T10:00:01+00:00"
    with pytest.raises(ValueError, match="DEADLINE"):
        review.verify_original_admission(freeze, acceptance, amendment, contract, 141)


def test_candidate_coverage_retains_unknown_lifecycle_and_exact_exclusion():
    from datetime import date, datetime
    from fractions import Fraction

    from quanthus.event_library.rule_card import load_candidate_rule_card
    from quanthus.event_library.scan import (
        CandidateFeatureEvidence,
        CandidateScanShard,
        _RawCandidate,
    )

    card = load_candidate_rule_card(
        Path(__file__).resolve().parents[2]
        / "config/p4-candidate-price-volume-transition.research.v1.json"
    )
    days = ["2012-01-04", "2012-01-05", "2012-01-06"]

    def raw(sid, day, industry):
        day = date.fromisoformat(day)
        feature = CandidateFeatureEvidence(
            feature_start=date(2011, 12, 1),
            signal_date=day,
            t_minus_1_range_activity_ratio=Fraction(1),
            t_minus_1_turnover_activity_ratio=Fraction(1),
            t_range_activity_ratio=Fraction(1),
            t_turnover_activity_ratio=Fraction(1),
            source_evidence_digests=("sha256:" + "a" * 64,),
        )
        return _RawCandidate(
            security_id=sid,
            listing_id="listing:" + sid,
            listing_code=sid,
            company_id="company:" + sid,
            industry_id=industry,
            signal_date=day,
            signal_close=datetime.fromisoformat(str(day) + "T15:00:00+08:00"),
            knowledge_cutoff=datetime.fromisoformat("2026-10-25T00:00:00+00:00"),
            feature_start=feature.feature_start,
            entry_date=date(2012, 1, 6),
            feature_evidence=feature,
            eligibility_evidence_digest="sha256:" + "b" * 64,
            rank_digest=sid,
        )

    shard = CandidateScanShard(
        raw_candidates=(
            raw("a", days[0], "one"),
            raw("a", days[1], "one"),
            raw("b", days[0], None),
            raw("c", days[1], "one"),
        ),
        security_ids=("a", "b", "c"),
        reason_counts={},
        evaluated_rows=4,
        series_count=3,
        decoded_rows=4,
        row_decode_elapsed_seconds=0,
        security_scan_elapsed_seconds=0,
    )
    preparation = {
        "raw_triggers": 4,
        "isolated": 3,
        "industry_gap_identities": 1,
        "retained_candidates": 2,
        "candidates": [
            {"security_id": "a", "signal_date": days[0]},
            {"security_id": "c", "signal_date": days[1]},
        ],
    }
    result = attribution.coverage(shard.to_dict(), preparation, days, card)
    assert result["totals"]["cooldown_excluded"] == 1
    assert result["totals"]["industry_excluded"] == 1
    assert result["totals"]["retained"] == 2
    assert all(
        r["historical_listing_lifecycle"] == "NOT_ESTABLISHED_BY_CANDIDATE_CENSUS"
        for r in result["by_identity"]
    )
    preparation["candidates"][0]["security_id"] = "b"
    with pytest.raises(ValueError, match="FROZEN_RETAINED"):
        attribution.coverage(shard.to_dict(), preparation, days, card)
