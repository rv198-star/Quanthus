"""Outcome-blind qualification and controlled R10 study orchestration."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quanthus.aggregator import GovernedAction
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.r10_inputs import NativeInputs, check_contract, read, sha, write
from quanthus.r10_learning import (
    begin_scheduled_correction,
    dump_scheduled_correction,
    resume_scheduled_correction,
    scheduled_score,
)
from quanthus.r10_replay import ControlledReplay
from quanthus.stage_learning import head2_revision as hr
from quanthus.stage_learning import local_head as lh


def admitted(contract: Path, root: Path):
    c = read(contract)
    check_contract(c)
    admission = read(root / "A-admission.safe.json")
    freeze_path = root / "source-freeze.safe.json"
    if (
        not admission["result_execution_ready"]
        or admission["contract_sha256"] != sha(contract)
        or admission["source_freeze_sha256"] != sha(freeze_path)
    ):
        raise ValueError("R10_A_ADMISSION_CHAIN")
    freeze = read(freeze_path)
    if freeze["contract_sha256"] != sha(contract):
        raise ValueError("R10_A_ADMISSION_CONTRACT")
    for name, expected in (
        ("selected.private.json", freeze["selection_sha256"]),
        ("E.private.json", freeze["E_sha256"]),
        ("E-cases.private.json", freeze["E_cases_sha256"]),
        ("A-v0-predictions.private.json", admission["prediction_sha256"]),
        ("A-actions.private.json", admission["action_sha256"]),
    ):
        if sha(root / name) != expected:
            raise ValueError("R10_A_FROZEN_INPUT_CHANGED:" + name)
    return c, admission


def review_reference(contract: Path, root: Path, role: str, qa: Path):
    from quanthus import r9_calibration as cal

    admitted(contract, root)
    ref = root.parent / "reference"
    result = cal.summarize(
        ref / (role + "-request.json"),
        ref / (role + "-selected.private.json"),
        ref / role / "references.sealed.json",
        qa,
        ref / ("qa-" + role),
    )
    labels = {r["case_id"]: r for r in result["cases"]}
    resolved = []
    for wrapped in read(root / "selected.private.json")["rows"]:
        r = wrapped["source_row"]
        i = r["identity"]
        if i["role"] == role:
            resolved.append(
                dict(
                    labels[i["sample_id"]],
                    company_id=i["company_id"],
                    anchor=i["anchor"],
                    signal_date=i["signal_date"],
                )
            )
    counts = Counter(r["label"] for r in resolved)
    companies = {
        k: len({r["company_id"] for r in resolved if r["label"] == k}) for k in ("TRUE", "FALSE")
    }
    months = sorted({r["signal_date"][:7] for r in resolved if r["label"] != "UNKNOWN"})
    passed = min(companies.values()) >= (2 if role == "guard" else 5)
    if role == "evaluation":
        passed &= counts["TRUE"] + counts["FALSE"] >= 12 and len(months) >= 2
    path = ref / ("qa-" + role) / "resolved.private.json"
    write(path, {"role": role, "resolved": resolved, "reference_summary": result})
    safe = {
        "role": role,
        "subjects": len(resolved),
        "counts": dict(counts),
        "companies_by_class": companies,
        "definite_signal_months": months,
        "support_pass": passed,
        "resolved_sha256": sha(path),
        "qa_sha256": sha(qa),
        "contract_sha256": sha(contract),
        "new_updates": 0,
    }
    write(ref / ("support-" + role + ".safe.json"), safe)
    return safe


def load_bound(c, path):
    p = Path(path)
    if sha(p) != c["asset_hashes"][str(p)]:
        raise ValueError("R10_IMPORTED_ASSET_BINDING")
    return read(p)


def fit_correction(contract: Path, root: Path):
    """See evaluation support only; seal predictions before evaluation labels."""
    from quanthus.r9_study import head_rows

    c, admission = admitted(contract, root)
    ref = root.parent / "reference"
    if datetime.now(timezone.utc) >= datetime.fromisoformat(admission["B_deadline"]):
        raise ValueError("R10_B_DEADLINE")
    supports = {
        role: read(ref / ("support-" + role + ".safe.json")) for role in ("guard", "evaluation")
    }
    if any(
        not s["support_pass"]
        or s["contract_sha256"] != sha(contract)
        or s["role"] != role
        or s["subjects"] != (8 if role == "guard" else 24)
        for role, s in supports.items()
    ):
        raise ValueError("R10_REFERENCE_SUPPORT_STOP")
    # Only the guard labels are opened; evaluation is still sealed to the fitter.
    guard_path = ref / "qa-guard/resolved.private.json"
    if sha(guard_path) != supports["guard"]["resolved_sha256"]:
        raise ValueError("R10_GUARD_BINDING")
    current = [r["source_row"] for r in read(root / "selected.private.json")["rows"]]
    old = load_bound(c, c["old_assets"]["selected"])["rows"]
    train_labels = load_bound(c, c["old_assets"]["train_reference"])["resolved"]
    update_labels = load_bound(c, c["old_assets"]["update_reference"])["resolved"]
    groups = (
        head_rows([r for r in old if r["identity"]["role"] == "train"], train_labels),
        head_rows([r for r in old if r["identity"]["role"] == "guard"], update_labels),
        head_rows(
            [r for r in current if r["identity"]["role"] == "guard"], read(guard_path)["resolved"]
        ),
    )
    v0 = parent(c)
    output = root.parent / "fit"
    output.mkdir()
    write(
        output / "attempt.json",
        {
            "contract_sha256": sha(contract),
            "parent_sha256": c["old_assets"]["parent_sha256"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "max_updates": 1,
        },
    )
    child = begin_scheduled_correction(
        v0,
        *groups,
        contract_digest="sha256:" + sha(contract),
        parent_file_sha256="sha256:" + c["old_assets"]["parent_sha256"],
        stop_after=1,
    )
    logs = []
    for step in range(1, 101):
        if step > 1:
            child = resume_scheduled_correction(child, *groups, stop_after=step)
        logs.append(
            {"step": step, "weights": list(child.update.weights), "bias": child.update.bias}
        )
        if step == 17:
            (output / "checkpoint-17.json").write_bytes(dump_scheduled_correction(child))
    (output / "v0.json").write_bytes(lh.dump_checkpoint(v0))
    (output / "v1.json").write_bytes(dump_scheduled_correction(child))
    write(output / "steps.private.json", {"rows": logs})
    retention = asdict(hr.retention_report(child.update, *groups))
    changed = child.update.weights != v0.weights or child.update.bias != v0.bias
    if not changed:
        raise ValueError("R10_UPDATE_NO_NUMERIC_CHANGE")
    write(
        output / "retention-choice.json",
        {
            "retention": retention,
            "preselected_version": "v1" if retention["accepted"] else "v0",
            "update_status": "UPDATED",
            "steps": 100,
            "numeric_change": changed,
            "formal_adoption": "NOT_GRANTED_LEGACY_EXPOSURE_UNKNOWN",
        },
    )
    cases = read(root / "E-cases.private.json")["rows"] + current
    cases += [r for r in old if r["identity"]["role"] in ("train", "guard")]
    predictions = []
    for row in cases:
        predictions.append(
            {
                "case_id": row["identity"]["sample_id"],
                "event_id": row.get("event_id"),
                "role": row["identity"]["role"],
                "v0": lh.predict_head(v0, row["features"]).raw_score if row["available"] else None,
                "v1": scheduled_score(child, row["features"]).raw_score
                if row["available"]
                else None,
            }
        )
    write(output / "predictions.sealed.json", {"rows": predictions})
    original_root = Path(c["old_assets"]["root"])
    published = original_root / "publication-manifest.json"
    if sha(published) != "d54cafeb0a8365e85e1d7a9a8ff6fa85c5071610d119c4bf8949fe51172b35c7":
        raise ValueError("R10_ORIGINAL_PUBLICATION_BINDING")
    manifest = {r["path"]: r for r in read(published)["files"]}
    original_predictions_path = original_root / "fit/predictions.sealed.json"
    if sha(original_predictions_path) != manifest["study/fit/predictions.sealed.json"]["sha256"]:
        raise ValueError("R10_ORIGINAL_PREDICTIONS_BINDING")
    original_predictions = read(original_predictions_path)["rows"]
    if len(original_predictions) != 64:
        raise ValueError("R10_ORIGINAL_PREDICTIONS_COUNT")
    old_by_id = {r["identity"]["sample_id"]: r for r in old}
    restored = [
        lh.predict_head(v0, old_by_id[r["case_id"]]["features"]).raw_score
        for r in original_predictions
    ]
    if any(a != b["v0"] for a, b in zip(restored, original_predictions, strict=True)):
        raise ValueError("R10_ORIGINAL_64_RESTORATION")
    write(
        output / "original-restoration.safe.json",
        {
            "original_parent_sha256": c["old_assets"]["parent_sha256"],
            "restored_parent_sha256": sha(output / "v0.json"),
            "predictions": len(restored),
            "max_absolute_difference": 0,
            "original_R9_update_status": "NO_UPDATE",
        },
    )
    result = {
        "contract_sha256": sha(contract),
        "selected_sha256": sha(root / "selected.private.json"),
        "files": {p.name: sha(p) for p in sorted(output.iterdir()) if p.name != "attempt.json"},
        "supports": supports,
        "steps": 100,
        "new_fits": 0,
        "new_updates": 1,
        "train_replay_rows": len(groups[0]),
        "update_definite_rows": len(groups[1]),
        "update_group_denominator": 8,
        "guard_definite_rows": len(groups[2]),
        "retention": retention,
        "evaluation_label_files_opened": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    write(output / "manifest.json", result)
    return result


def compare(contract: Path, root: Path):
    from quanthus.r9_stratified import r0_support
    from quanthus.stage_learning.order_rule_screen import load_rule

    c, _ = admitted(contract, root)
    fit = root.parent / "fit"
    sealed = read(fit / "manifest.json")
    if (
        sealed["contract_sha256"] != sha(contract)
        or sealed["selected_sha256"] != sha(root / "selected.private.json")
        or sealed["evaluation_label_files_opened"] != 0
    ):
        raise ValueError("R10_PREDICTION_COMMITMENT_BINDING")
    for name, expected in sealed["files"].items():
        if sha(fit / name) != expected:
            raise ValueError("R10_PREDICTION_COMMITMENT_CHANGED")
    support = read(root.parent / "reference/support-evaluation.safe.json")
    if support != sealed["supports"]["evaluation"]:
        raise ValueError("R10_EVALUATION_COMMITMENT_CHANGED")
    labels_path = root.parent / "reference/qa-evaluation/resolved.private.json"
    if sha(labels_path) != support["resolved_sha256"] or not support["support_pass"]:
        raise ValueError("R10_EVALUATION_SUPPORT_BINDING")
    label_package = read(labels_path)
    labels = {r["case_id"]: r["label"] for r in label_package["resolved"]}
    expected_cases = {
        r["source_row"]["identity"]["sample_id"]
        for r in read(root / "selected.private.json")["rows"]
        if r["source_row"]["identity"]["role"] == "evaluation"
    }
    if (
        label_package["role"] != "evaluation"
        or support["role"] != "evaluation"
        or support["contract_sha256"] != sha(contract)
        or support["subjects"] != 24
        or set(labels) != expected_cases
        or len(label_package["resolved"]) != 24
        or any(label not in ("TRUE", "FALSE", "UNKNOWN") for label in labels.values())
    ):
        raise ValueError("R10_EVALUATION_IDENTITY_SET")
    predictions = {r["case_id"]: r for r in read(fit / "predictions.sealed.json")["rows"]}
    rule = load_rule(Path(__file__).resolve().parents[2])
    metrics = {k: dict.fromkeys(("TP", "FP", "TN", "FN", "UNKNOWN"), 0) for k in ("r0", "v0", "v1")}
    details = []
    for wrapped in read(root / "selected.private.json")["rows"]:
        row = wrapped["source_row"]
        if row["identity"]["role"] != "evaluation":
            continue
        key = row["identity"]["sample_id"]
        label = labels[key]
        scores = predictions[key]
        states = {"r0": r0_support(row["primary_facts"], rule)}
        states.update({k: None if scores[k] is None else scores[k] >= 0.5 for k in ("v0", "v1")})
        for version, state in states.items():
            outcome = (
                "UNKNOWN"
                if label == "UNKNOWN" or state is None
                else (
                    "TP"
                    if label == "TRUE" and state
                    else "FN"
                    if label == "TRUE"
                    else "FP"
                    if state
                    else "TN"
                )
            )
            metrics[version][outcome] += 1
        details.append({"case_id": key, "label": label, "states": states})
    write(fit / "evaluation-unblinded.private.json", {"rows": details})
    recognition = metrics["v1"]["FN"] < metrics["v0"]["FN"] and (
        metrics["v1"]["FP"] <= metrics["v0"]["FP"]
    )
    result = {
        "contract_sha256": sha(contract),
        "prediction_commitment_sha256": sha(fit / "manifest.json"),
        "metrics": metrics,
        "recognition_gate": recognition,
        "retention_gate": sealed["retention"]["accepted"],
        "formal_adoption": "NOT_GRANTED_LEGACY_EXPOSURE_UNKNOWN",
        "C2_use": "CONTROLLED_DIAGNOSTIC"
        if not sealed["retention"]["accepted"]
        else "CONTROLLED_ENGINEERING_COMPARISON",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    write(fit / "comparison.safe.json", result)
    return result


def reference_payload(request: Path):
    from quanthus.r9_calibration import RUBRIC

    c = read(request)
    contract = Path(c["contract_path"])
    root = Path(c["qualification_root"])
    admitted(contract, root)
    admission = read(root / "A-admission.safe.json")
    if (
        c["contract_sha256"] != sha(contract)
        or c["admission_sha256"] != sha(root / "A-admission.safe.json")
        or c["selected_sha256"] != sha(root / "selected.private.json")
        or not admission["result_execution_ready"]
        or c["role"] not in ("guard", "evaluation")
    ):
        raise ValueError("R10_REFERENCE_ADMISSION_BINDING")
    rows = [
        r
        for r in read(root / "selected.private.json")["rows"]
        if r["source_row"]["identity"]["role"] == c["role"]
    ]
    if len(rows) != (8 if c["role"] == "guard" else 24):
        raise ValueError("R10_REFERENCE_ROLE_COUNT")
    return {
        "request_sha256": sha(request),
        "rubric_sha256": hashlib.sha256(RUBRIC.encode()).hexdigest(),
        "rows": rows,
    }


def prepare_references(contract: Path, root: Path):
    c, admission = admitted(contract, root)
    output = root.parent / "reference"
    output.mkdir()
    for role, count in (("guard", 8), ("evaluation", 24)):
        request = {
            "schema_version": "quanthus.r10-controlled-reference.v1",
            "role": role,
            "contract_path": str(contract.resolve()),
            "contract_sha256": sha(contract),
            "qualification_root": str(root.resolve()),
            "admission_sha256": sha(root / "A-admission.safe.json"),
            "selected_sha256": sha(root / "selected.private.json"),
            "reference": dict(c["reference"], maximum_calls=count * 2),
            "runtime": dict(
                c["runtime"],
                calibration_deadline=admission["B_deadline"],
                attempt_marker=str(output / (role + "-attempt.json")),
                budget_root=str(root.parent),
                output_bytes=c["runtime"]["job_new_bytes"],
                sandbox_budget_root=str(output / "sandbox"),
                tmp_bytes=134217728,
            ),
        }
        path = output / (role + "-request.json")
        write(path, request)
        write(output / (role + "-selected.private.json"), reference_payload(path))
    return {"requests": 2, "maximum_new_reference_starts": 64, "output": str(output)}


def parent(c):
    path = Path(c["old_assets"]["parent"])
    raw = path.read_bytes()
    expected = c["old_assets"]["parent_sha256"]
    if sha(path) != expected or c["asset_hashes"][str(path)] != expected:
        raise ValueError("R10_PARENT_BYTE_BINDING")
    return lh.load_checkpoint(
        raw,
        expected_sha256="sha256:" + expected,
        bindings=lh.HeadBindings(**read(path)["bindings"]),
    )


def screen(project: Path, contract: Path, root: Path):
    c = read(contract)
    check_contract(c)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(c["runtime"]["A_deadline"]):
        raise ValueError("R10_A_DEADLINE")
    freeze = read(root / "source-freeze.safe.json")
    for name, expected in (
        ("E.private.json", freeze["E_sha256"]),
        ("selected.private.json", freeze["selection_sha256"]),
        ("E-cases.private.json", freeze["E_cases_sha256"]),
    ):
        if sha(root / name) != expected:
            raise ValueError("R10_A_SOURCE_FREEZE_BINDING")
    if freeze["contract_sha256"] != sha(contract):
        raise ValueError("R10_A_CONTRACT_BINDING")
    v0 = parent(c)
    cases = read(root / "E-cases.private.json")["rows"]
    prediction_path = root / "A-v0-predictions.private.json"
    predictions = {
        "rows": [
            {
                "event_id": r["event_id"],
                "case_id": r["identity"]["sample_id"],
                "v0": lh.predict_head(v0, r["features"]).raw_score if r["available"] else None,
                "v1": None,
            }
            for r in cases
        ]
    }
    write(prediction_path, predictions)
    details = []
    native = NativeInputs(project, contract, root / "native")
    try:
        with ControlledReplay(project, native, root / "E.private.json", prediction_path) as run:
            runner = FormalStrategyRunner(run.registry.card, run.registry.topology_manifest.digest)
            for frame in run.frames:
                original = run._entry_candidates(frame, runner, None)
                confirmation = run._entry_candidates(frame, runner, "r10-v0")
                for b, b0 in zip(original, confirmation, strict=True):
                    event = b.item.record.event_id
                    score = run.predictions[event]["v0"]
                    details.append(
                        {
                            "event_id": event,
                            "company_id": run.event_rows[event]["company_id"],
                            "signal_date": str(frame.snapshot.market_date),
                            "B": b.decision.action.final_action.value,
                            "B_v0": b0.decision.action.final_action.value,
                            "score_known": score is not None,
                            "known_score_difference": score is not None
                            and b.decision.action.final_action is GovernedAction.TRIAL_ENTRY
                            and b0.decision.action.final_action is GovernedAction.NO_ACTION,
                        }
                    )
        write(root / "A-actions.private.json", {"rows": details})
        differences = [r for r in details if r["known_score_difference"]]
        company_count = len({r["company_id"] for r in differences})
        date_count = len({r["signal_date"] for r in differences})
        passed = company_count >= 2 and date_count >= 2
        now = datetime.now(timezone.utc)
        total = datetime.fromisoformat(c["runtime"]["total_deadline"])
        b_end = min(now + timedelta(hours=4), total - timedelta(hours=5))
        result = {
            "status": "A_QUALIFIED" if passed else "A_FUTILITY_STOP",
            "result_execution_ready": passed,
            "contract_sha256": sha(contract),
            "source_freeze_sha256": sha(root / "source-freeze.safe.json"),
            "prediction_sha256": sha(prediction_path),
            "action_sha256": sha(root / "A-actions.private.json"),
            "E": len(details),
            "B_actions": dict(Counter(r["B"] for r in details)),
            "differences": len(differences),
            "difference_companies": company_count,
            "difference_signal_dates": date_count,
            "legacy_exposure": "UNKNOWN_DIAGNOSTIC_ONLY",
            "consumer_admission": "NOT_GRANTED",
            "new_reference_calls": 0,
            "new_updates": 0,
            "new_capital_trials": 0,
            "completed_at": now.isoformat(),
            "B_deadline": b_end.isoformat(),
            "C_deadline": (b_end + timedelta(hours=3)).isoformat(),
            "total_deadline": total.isoformat(),
            "native_source_digest": native.content_digest,
        }
        write(root / "A-admission.safe.json", result)
        return result
    finally:
        native.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["screen", "prepare-references", "review-reference", "fit", "compare"]
    )
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--role", choices=["guard", "evaluation"])
    parser.add_argument("--qa", type=Path)
    args = parser.parse_args()
    if args.command == "screen":
        result = screen(Path(__file__).resolve().parents[2], args.contract, args.root)
    elif args.command == "prepare-references":
        result = prepare_references(args.contract, args.root)
    elif args.command == "review-reference" and args.role and args.qa:
        result = review_reference(args.contract, args.root, args.role, args.qa)
    elif args.command == "fit":
        result = fit_correction(args.contract, args.root)
    elif args.command == "compare":
        result = compare(args.contract, args.root)
    else:
        parser.error("review-reference requires --role and --qa")
    print(
        __import__("json").dumps(
            result,
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
