"""One prospective R9 study on the existing qualified pool; no acquisition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from quanthus import r9_calibration as cal
from quanthus.r9_learning import support
from quanthus.r9_qualification import check_roles
from quanthus.r9_reference import strict_json
from quanthus.r9_stratified import order, r0_support
from quanthus.stage_learning import head2_revision as hr
from quanthus.stage_learning import local_head as lh
from quanthus.stage_learning.order_rule_screen import load_rule
from quanthus.structure_v2_inputs import canonical

SCHEMA = "quanthus.r9-prospective-study.v1"
REFERENCE_SCHEMA = "quanthus.r9-study-reference.v1"
ROLES = ("guard", "evaluation", "train", "update")
CAPS = {"train": 24, "guard": 8, "update": 8, "evaluation": 24}
LAYOUT = (
    ("train", "2020-03-31", "boundary", 6),
    ("train", "2020-03-31", "expanding", 6),
    ("train", "2020-04-30", "narrow", 8),
    ("train", "2020-04-30", "boundary", 4),
    ("guard", "2020-08-31", "narrow", 4),
    ("guard", "2020-08-31", "boundary", 2),
    ("guard", "2020-08-31", "expanding", 2),
    ("update", "2020-08-31", "hash", 8),
    ("evaluation", "2020-11-30", "narrow", 4),
    ("evaluation", "2020-11-30", "boundary", 4),
    ("evaluation", "2020-11-30", "expanding", 4),
    ("evaluation", "2020-12-31", "narrow", 4),
    ("evaluation", "2020-12-31", "boundary", 4),
    ("evaluation", "2020-12-31", "expanding", 4),
)


def read(path: Path) -> Any:
    return strict_json(path.read_text())


def bound(path: Path, expected: str) -> Any:
    if cal.sha(path) != expected:
        raise ValueError("R9_STUDY_FILE_BINDING")
    return read(path)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def contract(request: Path) -> dict[str, Any]:
    c = read(request)
    expected_policy = json.loads(json.dumps({**asdict(hr.revision_policy()), "raw_threshold": 0.5}))
    if (
        c["schema_version"] != SCHEMA
        or c["maximum_calls"] != 128
        or c["learning"] != expected_policy
        or c["selection"]["layout"] != [list(row) for row in LAYOUT]
    ):
        raise ValueError("R9_STUDY_FROZEN_CONTRACT")
    for name, expected in c["implementation_bindings"].items():
        if cal.sha(Path(__file__).resolve().parents[2] / name) != expected:
            raise ValueError("R9_STUDY_CODE_BINDING")
    return cast(dict[str, Any], c)


def root_for(request: Path) -> Path:
    return Path(contract(request)["output_root"])


def select_rows(pool: list[dict[str, Any]], excluded: set[str]) -> tuple[list[Any], list[Any]]:
    """Freeze all identities by unlabeled proxies; never repair a shortage."""
    if len(pool) != 1536 or len({r["identity"]["company_id"] for r in pool}) != 1536:
        raise ValueError("R9_STUDY_POOL_DENOMINATOR")
    selected: list[Any] = []
    capacity = []
    used = set(excluded)

    def number(row: dict[str, Any], key: str) -> Decimal:
        result = Decimal(row["primary_facts"][key]["value"])
        if not result.is_finite():
            raise ValueError("R9_STUDY_NONFINITE_PROXY")
        return result

    for role, anchor, proxy, count in LAYOUT:
        eligible = [
            r
            for r in pool
            if r["available"]
            and r["identity"]["anchor"] == anchor
            and r["identity"]["company_id"] not in used
        ]
        if proxy in ("narrow", "boundary"):
            eligible = sorted(
                [
                    r
                    for r in eligible
                    if abs(number(r, "directional_efficiency")) <= Decimal("0.33")
                    and number(r, "width_change") <= Decimal("-0.20")
                ],
                key=lambda r: (number(r, "range_width"), r["identity"]["sample_id"]),
            )
        elif proxy == "expanding":
            eligible = sorted(
                [r for r in eligible if number(r, "width_change") > 0],
                key=lambda r: (-number(r, "width_change"), r["identity"]["sample_id"]),
            )
        else:
            eligible = sorted(eligible, key=lambda r: order(r["identity"]))
        capacity.append(
            {
                "role": role,
                "anchor": anchor,
                "proxy": proxy,
                "available_candidates": len(eligible),
                "required": count,
            }
        )
        if len(eligible) < count:
            raise ValueError("R9_STUDY_CAPACITY_STOP:" + json.dumps(capacity))
        start = (len(eligible) - count) // 2 if proxy == "boundary" else 0
        for row in eligible[start : start + count]:
            used.add(row["identity"]["company_id"])
            selected.append(
                {
                    **row,
                    "source_identity_role": row["identity"]["role"],
                    "identity": {**row["identity"], "role": role},
                    "sampling_proxy": proxy,
                }
            )
    check_roles({"rows": [r["identity"] for r in selected]})
    return selected, capacity


def prepare(request: Path) -> dict[str, Any]:
    c = contract(request)
    check_deadline(c)
    root = Path(c["output_root"])
    if root.exists():
        raise ValueError("R9_STUDY_FRESH_OUTPUT")
    source = c["source"]
    pool = bound(Path(source["pool_path"]), source["pool_sha256"])["rows"]
    excluded = set()
    for entry in source["exclusions"]:
        data = bound(Path(entry["path"]), entry["sha256"])
        for row in data["rows"]:
            excluded.add(row.get("source_row", row)["identity"]["company_id"])
    anchors = bound(Path(c["calibration"]["summary_path"]), c["calibration"]["summary_sha256"])
    if anchors["panel_qualified"] is not True:
        raise ValueError("R9_STUDY_CALIBRATION_GATE")
    rows, capacity = select_rows(pool, excluded)
    root.mkdir(parents=True)
    cal.write(root / "selected.private.json", {"request_sha256": cal.sha(request), "rows": rows})
    cal.write(root / "role-identities.private.json", {"rows": [r["identity"] for r in rows]})
    chosen = {r["identity"]["company_id"] for r in rows}
    cal.write(
        root / "selection-denominator.private.json",
        {
            "rows": [
                {
                    "company_id": r["identity"]["company_id"],
                    "anchor": r["identity"]["anchor"],
                    "available": r["available"],
                    "reason": r["reason"],
                    "excluded_company": r["identity"]["company_id"] in excluded,
                    "selected": r["identity"]["company_id"] in chosen,
                }
                for r in pool
            ],
            "capacity": capacity,
        },
    )
    result = {
        "request_sha256": cal.sha(request),
        "subjects": len(rows),
        "selected_sha256": cal.sha(root / "selected.private.json"),
        "identity_sha256": cal.sha(root / "role-identities.private.json"),
        "excluded_companies": len(excluded),
        "capacity": capacity,
        "fit_runs": 0,
        "update_runs": 0,
        "reference_calls": 0,
    }
    cal.write(root / "selection.safe.json", result)
    return result


def selected_rows(request: Path) -> list[dict[str, Any]]:
    root = root_for(request)
    receipt = read(root / "selection.safe.json")
    if receipt["request_sha256"] != cal.sha(request):
        raise ValueError("R9_STUDY_SELECTION_REQUEST")
    return cast(
        list[dict[str, Any]],
        bound(root / "selected.private.json", receipt["selected_sha256"])["rows"],
    )


def role_support(request: Path, role: str) -> dict[str, Any]:
    root = root_for(request)
    safe = read(root / f"support-{role}.safe.json")
    if safe["request_sha256"] != cal.sha(request) or safe["role"] != role:
        raise ValueError("R9_STUDY_SUPPORT_BINDING")
    if safe["support_pass"] is not True:
        raise ValueError("R9_STUDY_SUPPORT_STOP:" + role)
    return cast(dict[str, Any], safe)


def reference_payload(request: Path) -> dict[str, Any]:
    c = read(request)
    study = Path(c["study_request_path"])
    if cal.sha(study) != c["study_request_sha256"]:
        raise ValueError("R9_STUDY_REFERENCE_REQUEST")
    contract(study)
    role = c["role"]
    for prior in ROLES[: ROLES.index(role)]:
        bound(root_for(study) / f"support-{prior}.safe.json", c["prior_support_sha256"][prior])
        role_support(study, prior)
    rows = selected_rows(study)
    if c["selected_sha256"] != cal.sha(root_for(study) / "selected.private.json"):
        raise ValueError("R9_STUDY_REFERENCE_SELECTION")
    return {
        "request_sha256": cal.sha(request),
        "rubric_sha256": hashlib.sha256(cal.RUBRIC.encode()).hexdigest(),
        "rows": [
            {"sampling_intent": r["sampling_proxy"], "source_row": r}
            for r in rows
            if r["identity"]["role"] == role
        ],
    }


def collect_role(request: Path, role: str) -> dict[str, Any]:
    c, root = contract(request), root_for(request)
    for prior in ROLES[: ROLES.index(role)]:
        role_support(request, prior)
    now = datetime.now(timezone.utc)
    window = root / "reference-window.json"
    if not window.exists():
        with window.open("x") as stream:
            deadline = min(
                now + timedelta(seconds=7200),
                datetime.fromisoformat(c["runtime"]["reference_deadline"]),
            )
            json.dump({"started_at": now.isoformat(), "deadline": deadline.isoformat()}, stream)
    deadline = read(window)["deadline"]
    role_request = root / f"request-{role}.json"
    role_config = {
        "schema_version": REFERENCE_SCHEMA,
        "role": role,
        "study_request_path": str(request.resolve()),
        "study_request_sha256": cal.sha(request),
        "selected_sha256": cal.sha(root / "selected.private.json"),
        "prior_support_sha256": {
            prior: cal.sha(root / f"support-{prior}.safe.json")
            for prior in ROLES[: ROLES.index(role)]
        },
        "reference": {
            "models": list(cal.MODELS),
            "provider_id": "magpie",
            "maximum_calls": CAPS[role] * 2,
            "retries": 0,
            "concurrency": 2,
        },
        "runtime": {
            **c["runtime"],
            "budget_root": str(root),
            "sandbox_budget_root": str(root / "sandboxes"),
            "calibration_deadline": deadline,
            "attempt_marker": str(root / f"attempt-{role}.json"),
        },
    }
    if role_request.exists():
        raise ValueError("R9_STUDY_ROLE_SINGLE_ATTEMPT")
    cal.write(role_request, role_config)
    selection = root / f"selection-{role}"
    cal.prepare(role_request, selection)
    return cal.collect(
        role_request,
        selection / "selected.private.json",
        root / f"reference-{role}",
        root / f"sandboxes/{role}",
    )


def review_role(request: Path, role: str, qa: Path) -> dict[str, Any]:
    root = root_for(request)
    result = cal.summarize(
        root / f"request-{role}.json",
        root / f"selection-{role}/selected.private.json",
        root / f"reference-{role}/references.sealed.json",
        qa,
        root / f"qa-{role}",
    )
    by_id = {r["case_id"]: r for r in result["cases"]}
    resolved = []
    for row in selected_rows(request):
        identity = row["identity"]
        if identity["role"] == role:
            resolved.append(
                {
                    **by_id[identity["sample_id"]],
                    "anchor": identity["anchor"],
                    "company_id": identity["company_id"],
                    "available": row["available"],
                }
            )
    summary = support(role, resolved)
    if role == "evaluation":
        companies = {
            label: len({r["company_id"] for r in resolved if r["label"] == label})
            for label in ("TRUE", "FALSE")
        }
        summary["unique_companies_by_class"] = companies
        summary["support_pass"] &= min(companies.values()) >= 5
    private = root / f"qa-{role}/resolved.private.json"
    cal.write(
        private,
        {"request_sha256": cal.sha(request), "role": role, "resolved": resolved, "summary": result},
    )
    safe = {
        **summary,
        "request_sha256": cal.sha(request),
        "resolved_sha256": cal.sha(private),
        "role_request_sha256": cal.sha(root / f"request-{role}.json"),
        "qa_sha256": cal.sha(qa),
        "references_sha256": result["opinions_sha256"],
        "fit_runs": 0,
        "update_runs": 0,
    }
    cal.write(root / f"support-{role}.safe.json", safe)
    return safe


def resolved_role(request: Path, role: str) -> list[dict[str, Any]]:
    safe = role_support(request, role)
    data = bound(root_for(request) / f"qa-{role}/resolved.private.json", safe["resolved_sha256"])
    if data["request_sha256"] != cal.sha(request) or data["role"] != role:
        raise ValueError("R9_STUDY_LABEL_BINDING")
    return cast(list[dict[str, Any]], data["resolved"])


def head_rows(rows: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> list[lh.HeadRow]:
    by_id = {r["case_id"]: r for r in resolved}
    result = []
    for row in rows:
        identity = row["identity"]
        label = by_id.get(identity["sample_id"], {}).get("label")
        if row["available"] and label in ("TRUE", "FALSE"):
            result.append(
                lh.HeadRow(
                    identity["sample_id"],
                    identity["security_id"],
                    identity["episode_id"],
                    identity["anchor"],
                    tuple(row["features"]),
                    int(label == "TRUE"),
                )
            )
    return result


def check_deadline(c: dict[str, Any]) -> None:
    if datetime.now(timezone.utc) >= datetime.fromisoformat(c["runtime"]["total_deadline"]):
        raise ValueError("R9_STUDY_DEADLINE")


def numeric_score(value: Any) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError("R9_STUDY_SCORE_UNAVAILABLE")
    return float(value)


def fit(request: Path, *, cold_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Never open evaluation label/QA files; only its support receipt is visible."""
    c, root = contract(request), root_for(request)
    check_deadline(c)
    supports = {role: role_support(request, role) for role in ROLES}
    rows = selected_rows(request) if cold_rows is None else cold_rows
    check_roles({"rows": [r["identity"] for r in rows]})
    groups = {
        role: head_rows(rows, resolved_role(request, role)) for role in ("train", "update", "guard")
    }
    hr.validate_roles(groups["train"], groups["update"], groups["guard"])
    output = root / ("fit" if cold_rows is None else "cold-fit")
    output.mkdir()  # Exclusive primary fit, and one separately declared cold reconstruction.
    cal.write(
        output / "attempt.json",
        {
            "request_sha256": cal.sha(request),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "max_fit": 1,
            "max_update": 1,
        },
    )
    bindings = lh.HeadBindings(
        request="sha256:" + cal.sha(request),
        source="sha256:" + c["source"]["pool_sha256"],
        reference=digest({r: supports[r]["resolved_sha256"] for r in ROLES}),
        eligibility=digest(supports),
        split=digest([r["identity"] for r in rows]),
        features=hr.feature_contract_digest(),
        target=digest({"rubric": cal.RUBRIC}),
        code=digest(c["implementation_bindings"]),
    )
    v0 = lh.fit_head(groups["train"], policy=hr.revision_policy(), bindings=bindings)
    (output / "v0.json").write_bytes(lh.dump_checkpoint(v0))
    diagnostic = lh.diagnose_head(v0, groups["update"])
    (output / "update-diagnostic.json").write_bytes(lh.dump_diagnostic(diagnostic))
    v1 = hr.begin_update(
        v0, groups["train"], groups["update"], groups["guard"], diagnostic=diagnostic
    )
    retention = None
    if v1 is not None:
        (output / "v1.json").write_bytes(hr.dump_update(v1))
        retention = asdict(
            hr.retention_report(v1, groups["train"], groups["update"], groups["guard"])
        )
    choice = "v1" if retention is not None and retention["accepted"] else "v0"
    state = {
        "update_status": "NO_UPDATE" if v1 is None else "UPDATED",
        "retention": retention,
        "preselected_version": choice,
        "update_raw_log_loss": diagnostic.raw_log_loss,
        "update_loss_trigger": hr.UPDATE_LOSS_TRIGGER,
    }
    cal.write(output / "retention-choice.json", state)
    rule = load_rule(Path(__file__).resolve().parents[2])
    predictions = []
    for row in rows:
        features = row["features"]
        predictions.append(
            {
                "case_id": row["identity"]["sample_id"],
                "role": row["identity"]["role"],
                "r0": r0_support(row["primary_facts"], rule),
                "v0": numeric_score(lh.predict_head(v0, features).raw_score),
                "v1": None
                if v1 is None
                else numeric_score(hr.update_score(v1, features).raw_score),
            }
        )
    cal.write(output / "predictions.sealed.json", {"rows": predictions})
    files = {
        p.name: cal.sha(p)
        for p in sorted(output.iterdir())
        if p.is_file() and p.name != "attempt.json"
    }
    check_deadline(c)
    result = {
        "request_sha256": cal.sha(request),
        "selected_sha256": cal.sha(root / "selected.private.json"),
        "bindings": asdict(bindings),
        "support_sha256": {r: cal.sha(root / f"support-{r}.safe.json") for r in ROLES},
        "files": files,
        "fit_runs": 1,
        "update_runs": int(v1 is not None),
        **state,
        "evaluation_label_files_opened": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    cal.write(output / "manifest.json", result)
    return result


def confusion(rows: list[dict[str, Any]], version: str) -> dict[str, int] | None:
    if not rows or any(row[version] is None for row in rows):
        return None
    result = dict.fromkeys(("TP", "TN", "FP", "FN"), 0)
    for row in rows:
        predicted = bool(row[version]) if version == "r0" else row[version] >= 0.5
        actual = row["label"] == "TRUE"
        result[("T" if predicted == actual else "F") + ("P" if predicted else "N")] += 1
    return result


def compare(request: Path, *, cold: bool = False) -> dict[str, Any]:
    c, root = contract(request), root_for(request)
    check_deadline(c)
    output = root / ("cold-fit" if cold else "fit")
    manifest = read(output / "manifest.json")
    if manifest["request_sha256"] != cal.sha(request) or manifest["selected_sha256"] != cal.sha(
        root / "selected.private.json"
    ):
        raise ValueError("R9_STUDY_PREDICTION_COMMITMENT")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or cal.sha(output / name) != expected:
            raise ValueError("R9_STUDY_COMMITMENT_TAMPERED")
    for role in ROLES:
        bound(root / f"support-{role}.safe.json", manifest["support_sha256"][role])
    with (output / "evaluation-unblind.attempt.json").open("x") as stream:
        json.dump(
            {
                "manifest_sha256": cal.sha(output / "manifest.json"),
                "opened_at": datetime.now(timezone.utc).isoformat(),
            },
            stream,
        )
    # The first evaluation label read is below the immutable prediction commitment.
    resolved = {r["case_id"]: r for role in ROLES for r in resolved_role(request, role)}
    predictions = read(output / "predictions.sealed.json")["rows"]
    source_rows = {r["identity"]["sample_id"]: r for r in selected_rows(request)}
    rows = []
    for prediction in predictions:
        identity = source_rows[prediction["case_id"]]["identity"]
        row = {
            **prediction,
            "anchor": identity["anchor"],
            "company_id": identity["company_id"],
            "label": resolved[prediction["case_id"]]["label"],
            "probability": None,
            "expert_disposition": "pending/ABSTAIN",
        }
        for version in ("v0", "v1"):
            row[version + "_positive"] = None if row[version] is None else row[version] >= 0.5
        rows.append(row)
    groups = {
        role: head_rows(list(source_rows.values()), resolved_role(request, role)) for role in ROLES
    }
    hr.validate_roles(groups["train"], groups["update"], groups["guard"], groups["evaluation"])
    evaluated = [r for r in rows if r["role"] == "evaluation" and r["label"] in ("TRUE", "FALSE")]
    metrics = {v: confusion(evaluated, v) for v in ("r0", "v0", "v1")}
    v0, v1 = metrics["v0"], metrics["v1"]
    adopted = bool(
        manifest["preselected_version"] == "v1"
        and v0 is not None
        and v1 is not None
        and v1["FP"] < v0["FP"]
        and v1["FN"] <= v0["FN"]
    )
    result = {
        "request_sha256": cal.sha(request),
        "fit_manifest_sha256": cal.sha(output / "manifest.json"),
        "subjects": len(rows),
        "evaluation_opportunities": 24,
        "evaluation_definite": len(evaluated),
        "evaluation_unknown": 24 - len(evaluated),
        "metrics": metrics,
        "decision": "choose-v1" if adopted else "retain-v0",
        "reason": "FP strictly decreased with FN nonincrease and retention"
        if adopted
        else (
            "loss trigger absent"
            if manifest["update_status"] == "NO_UPDATE"
            else "candidate did not pass all retention and evaluation adoption gates"
        ),
        "counts_by_role": {
            role: dict(Counter(r["label"] for r in rows if r["role"] == role)) for role in ROLES
        },
        "month_denominators": dict(Counter(r["anchor"][:7] for r in rows)),
        "probability": None,
        "expert_disposition": "pending/ABSTAIN",
        "use": "S02 proxy-enriched research version choice; Nov/Dec time-out evaluation",
    }
    check_deadline(c)
    cal.write(output / "s02-sidecar.private.json", {"summary": result, "rows": rows})
    cal.write(output / "comparison.safe.json", result)
    return result


def cold(request: Path) -> dict[str, Any]:
    """One independent feature and learner reconstruction; references are not revoted."""
    from quanthus.r9_qualification import qualify

    c, root = contract(request), root_for(request)
    check_deadline(c)
    original = read(root / "fit/manifest.json")
    original_comparison = read(root / "fit/comparison.safe.json")
    if original_comparison["fit_manifest_sha256"] != cal.sha(
        root / "fit/manifest.json"
    ) or original["request_sha256"] != cal.sha(request):
        raise ValueError("R9_STUDY_COLD_PRIMARY_BINDING")
    for name, expected in original["files"].items():
        if cal.sha(root / "fit" / name) != expected:
            raise ValueError("R9_STUDY_COLD_PRIMARY_TAMPERED")
    cold_root = root / "cold"
    cold_root.mkdir()
    source_contract = {
        "source": c["source"]["native_source"],
        "fixed_role_identity": {"sha256": cal.sha(root / "role-identities.private.json")},
    }
    cold_request = cold_root / "native-request.json"
    cal.write(cold_request, source_contract)
    qualification = qualify(
        Path(__file__).resolve().parents[2],
        Path(c["source"]["native_source"]["cache"]),
        root / "role-identities.private.json",
        cold_request,
        cold_root / "qualified",
        include_primary=True,
    )
    primary = selected_rows(request)
    rebuilt = read(cold_root / "qualified/qualified-cases.private.json")["rows"]
    by_id = {r["identity"]["sample_id"]: r for r in rebuilt}
    if len(rebuilt) != 64 or len(by_id) != 64:
        raise ValueError("R9_STUDY_COLD_DENOMINATOR")
    cold_rows = []
    for row in primary:
        other = by_id[row["identity"]["sample_id"]]
        if any(row[key] != other[key] for key in ("identity", "available", "features", "packet")):
            raise ValueError("R9_STUDY_COLD_FEATURE_MISMATCH")
        if any(
            row["primary_facts"][key]["value"] != value["value"]
            for key, value in other["primary_facts"].items()
        ):
            raise ValueError("R9_STUDY_COLD_PRIMARY_FACT_MISMATCH")
        cold_rows.append(
            {
                **row,
                "features": other["features"],
                "packet": other["packet"],
                "primary_facts": other["primary_facts"],
            }
        )
    reconstructed = fit(request, cold_rows=cold_rows)
    if reconstructed["files"] != original["files"]:
        raise ValueError("R9_STUDY_COLD_CHECKPOINT_MISMATCH")
    comparison = compare(request, cold=True)
    if any(
        comparison[key] != original_comparison[key] for key in ("metrics", "decision", "reason")
    ):
        raise ValueError("R9_STUDY_COLD_DECISION_MISMATCH")
    result = {
        "status": "COLD_FEATURE_FIT_UPDATE_PREDICTION_DECISION_MATCH",
        "qualified_subjects": qualification["admitted"],
        "reference_revotes": 0,
        "cold_fit_runs": 1,
        "cold_update_runs": reconstructed["update_runs"],
        "primary_fit_manifest_sha256": cal.sha(root / "fit/manifest.json"),
        "cold_fit_manifest_sha256": cal.sha(root / "cold-fit/manifest.json"),
        "qualification_sha256": cal.sha(cold_root / "qualified/qualification.safe.json"),
        "r0": "same frozen predicate independently recomputed from fresh native facts",
        "decision": comparison["decision"],
    }
    check_deadline(c)
    cal.write(cold_root / "receipt.safe.json", result)
    return result


def run_limited(request: Path, action: str) -> dict[str, Any]:
    """Reuse the bounded process guard for one fit/compare/cold worker."""
    c, root = contract(request), root_for(request)
    check_deadline(c)
    logs = root / f"runtime-{action}"
    logs.mkdir()
    sandbox = root / "sandboxes" / action
    sandbox.mkdir(parents=True)
    guard = cal.ResourceGuard(
        {**c["runtime"], "budget_root": str(root), "sandbox_budget_root": str(root / "sandboxes")},
        logs,
        sandbox,
    )
    remaining = (
        datetime.fromisoformat(c["runtime"]["total_deadline"]) - datetime.now(timezone.utc)
    ).total_seconds()
    cap = min(120 if action == "compare" else 600, remaining)
    started = time.monotonic()
    with (logs / "stdout.json").open("wb") as stdout, (logs / "stderr.txt").open("wb") as stderr:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "quanthus.r9_study",
                action,
                "--request",
                str(request.resolve()),
                "--worker",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        guard.processes.append(process)
        assert process.stdout is not None and process.stderr is not None
        threads = [
            threading.Thread(target=cal.capture_stream, args=(stream, sink, guard), daemon=True)
            for stream, sink in ((process.stdout, stdout), (process.stderr, stderr))
        ]
        guard.thread.start()
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=cap)
        except subprocess.TimeoutExpired:
            timed_out = True
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        finally:
            for thread in threads:
                thread.join(timeout=2)
            guard.done.set()
            guard.thread.join(timeout=2)
    receipt = {
        "phase": action,
        "seconds": round(time.monotonic() - started, 3),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "phase_wall_cap_seconds": cap,
        "peak_rss_bytes": guard.peak_rss,
        "cpu_affinity": guard.affinity,
        "stop_reason": guard.reason,
        "budget_root": str(root),
        "request_sha256": cal.sha(request),
    }
    cal.write(logs / "receipt.safe.json", receipt)
    if (
        process.returncode != 0
        or timed_out
        or guard.reason
        or any(thread.is_alive() for thread in threads)
    ):
        raise ValueError("R9_STUDY_WORKER_FAILED:" + action)
    check_deadline(c)
    if cal.directory_bytes(root) > c["runtime"]["output_bytes"]:
        raise ValueError("R9_STUDY_OUTPUT_LIMIT")
    result = read(logs / "stdout.json")
    result["runtime_receipt_sha256"] = cal.sha(logs / "receipt.safe.json")
    return cast(dict[str, Any], result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("prepare", "collect-role", "review-role", "fit", "compare", "cold")
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--qa", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.action in ("fit", "compare", "cold") and not args.worker:
        result = run_limited(args.request, args.action)
    elif args.action == "prepare":
        result = prepare(args.request)
    elif args.action == "collect-role" and args.role:
        result = collect_role(args.request, args.role)
    elif args.action == "review-role" and args.role and args.qa:
        result = review_role(args.request, args.role, args.qa)
    elif args.action == "fit":
        result = fit(args.request)
    elif args.action == "compare":
        result = compare(args.request)
    elif args.action == "cold":
        result = cold(args.request)
    else:
        parser.error("role actions require --role; review requires --qa; learning not activated")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
