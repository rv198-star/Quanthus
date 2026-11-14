"""Outcome-blind qualification and controlled R10 study orchestration."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quanthus.aggregator import GovernedAction
from quanthus.expert_strategy.runner import FormalStrategyRunner
from quanthus.r10_inputs import NativeInputs, check_contract, read, sha, write
from quanthus.r10_replay import ControlledReplay
from quanthus.stage_learning import local_head as lh


def reference_payload(request: Path):
    from quanthus.r9_calibration import RUBRIC

    c = read(request)
    contract = Path(c["contract_path"])
    root = Path(c["qualification_root"])
    check_contract(read(contract))
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
    c = read(contract)
    admission = read(root / "A-admission.safe.json")
    if not admission["result_execution_ready"]:
        raise ValueError("R10_REFERENCE_NOT_ADMITTED")
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
    parser.add_argument("command", choices=["screen", "prepare-references"])
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(
        __import__("json").dumps(
            screen(Path(__file__).resolve().parents[2], args.contract, args.root)
            if args.command == "screen"
            else prepare_references(args.contract, args.root),
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
