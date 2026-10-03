"""One governed B requalification of retained inputs; never reconstructs daily data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import tempfile
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.event_library.physical_scan import iter_projected_candidate_series
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import scan_forward_candidate_series
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy.p3_retained_audit import (
    calendar_audit,
    count,
    coverage_ledger,
    projection_audit,
    replay_report,
)
from quanthus.expert_strategy.p3_strat_003_input_qualification import (
    ARTIFACT_SCHEMA,
    PARTITION,
    REQUEST_DIGEST,
    _candidate_manifest,
    _qualification_evidence,
    _RecentSplitPolicy,
    _RecentVisibilityPolicy,
    _safe_relative,
    _sha256,
    _verify_replay_bundle,
    verify_p3_strat_003_input_artifact,
)
from quanthus.forward_validation.prospective_source import _schema_digest
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.policy import find_project_root

REQUEST_CONFIG = "config/research/p3-strat-003/retained-input.v1.json"
REQUEST_SHA = "sha256:b642509bd80bde69a5e765c5e8b7860e01b3643e33e576029e6788d343294ff4"


def request(path: Path) -> dict[str, Any]:
    if _sha256(path) != REQUEST_SHA:
        raise ValueError("RETAINED_REQUEST_DRIFT")
    raw = json.loads(path.read_bytes())
    project = find_project_root(path)
    for name in ("research_request", "activation"):
        binding = raw[name]
        if _sha256(project / binding["path"]) != binding["sha256"]:
            raise ValueError("RETAINED_AUTHORITY_DRIFT")
    research = json.loads((project / raw["research_request"]["path"]).read_bytes())
    if (
        digest_value({k: v for k, v in research.items() if k != "semantic_digest"})
        != REQUEST_DIGEST
    ):
        raise ValueError("FROZEN_RESEARCH_REQUEST_DRIFT")
    binding = research["bindings"]["candidate_rule"]
    if _sha256(project / binding["path"]) != binding["sha256"]:
        raise ValueError("FROZEN_CANDIDATE_RULE_DRIFT")
    return raw


def verify_inputs(root: Path, raw: dict[str, Any]) -> int:
    """All admitted files must be immutable byte matches before any Parquet read."""
    total = 0
    seen = set()
    for member in raw["files"]:
        relative = member["path"]
        if relative in seen:
            raise ValueError("DUPLICATE_RETAINED_SOURCE")
        seen.add(relative)
        path = _safe_relative(root, relative, "retained source")
        if any(p.is_symlink() for p in (root / relative, *(root / relative).parents)):
            raise ValueError("RETAINED_SOURCE_SYMLINK")
        if path.stat().st_size != member["size_bytes"] or _sha256(path) != member["sha256"]:
            raise ValueError("RETAINED_SOURCE_DRIFT:" + relative)
        total += path.stat().st_size
    return total


def _json(root: Path, name: str) -> dict[str, Any]:
    return json.loads((root / name).read_bytes())


def _write(path: Path, data: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(data) + b"\n")


class _BoundedReader(HistoricalReplayFrameReader):
    def _create_views(self) -> None:
        self._ensure_open().execute("SET max_temp_directory_size='8GiB'")
        super()._create_views()


class InputBudgetExceeded(BaseException):
    """Bypass broad third-party exception handlers without retrying a spent attempt."""


def _timeout(_signum: int, _frame: Any) -> None:
    raise InputBudgetExceeded("RETAINED_B_WALL_BUDGET_EXCEEDED")


def claim_attempt(path: Path, *, output: Path, revision: str) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("RETAINED_ATTEMPT_SYMLINK")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(
                canonical_json_bytes(
                    {
                        "request_sha256": REQUEST_SHA,
                        "code_revision": revision,
                        "output": str(output),
                        "maximum_attempts": 1,
                    }
                )
                + b"\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise ValueError("RETAINED_B_ATTEMPT_ALREADY_SPENT") from exc


def recover_retained_staging(
    run_root: Path,
    job_id: str,
    generation: int,
    code_revision: str,
) -> Path | None:
    raw = request(find_project_root() / REQUEST_CONFIG)
    receipt_path = Path(raw["attempt_receipt"])
    if not receipt_path.exists():
        return None
    if receipt_path.is_symlink():
        raise ValueError("RETAINED_ATTEMPT_SYMLINK")
    receipt = json.loads(receipt_path.read_bytes())
    prior = Path(receipt["output"])
    job_root = run_root.resolve() / ".staging" / job_id
    if (
        receipt["request_sha256"] != REQUEST_SHA
        or receipt["code_revision"] != code_revision
        or not prior.is_absolute()
        or prior.parent.parent != job_root
        or prior.name != "p3-retained-input"
        or not prior.parent.name.isdigit()
        or not 0 < int(prior.parent.name) <= generation
        or any(p.is_symlink() for p in (prior, *prior.parents))
    ):
        raise ValueError("RETAINED_B_ATTEMPT_ALREADY_SPENT_OR_IDENTITY_DIFFERS")
    if not (prior / "READY").is_file():
        raise ValueError("RETAINED_B_SPENT_ATTEMPT_HAS_NO_SEALED_RESULT")
    verify_retained_input_artifact(
        prior, code_revision=code_revision, request_digest=digest_value(raw)
    )
    return prior


def _scan(
    output: Path,
    project: Path,
    bundle: Any,
    calendar: dict[str, Any],
    industry: Any,
    target_manifest_sha: str,
    scope: dict[str, Any],
) -> Any:
    history, start, end, execution = (
        date.fromisoformat(scope[key])
        for key in ("history_start", "decision_start", "decision_end", "execution_end")
    )
    policy = _RecentVisibilityPolicy(
        history,
        start,
        end,
        execution,
        digest_value(
            {
                "request_digest": REQUEST_DIGEST,
                "bundle_digest": bundle.digest,
                "history_start": history.isoformat(),
                "decision_start": start.isoformat(),
                "decision_end": end.isoformat(),
                "execution_end": execution.isoformat(),
            }
        ),
    )
    with tempfile.TemporaryDirectory(prefix=".candidate-scan-", dir=output) as temporary:
        series = iter_projected_candidate_series(
            SimpleNamespace(candidate_path=output / "candidate-daily.parquet"),
            source_policy=policy,
            industry_mapping=industry,
            duckdb_memory_limit="6GB",
            duckdb_temp_directory=Path(temporary) / "spill",
            duckdb_max_temp_directory_size="8GiB",
        )
        try:
            return scan_forward_candidate_series(
                series,
                trading_dates=tuple(date.fromisoformat(d) for d in calendar["open_dates"]),
                snapshot_id=bundle.snapshot.snapshot_id,
                snapshot_manifest_digest=target_manifest_sha,
                card=load_candidate_rule_card(
                    project / "config/p4-candidate-price-volume-transition.research.v1.json"
                ),
                split_policy=_RecentSplitPolicy(history, start, end, execution),
                source_policy=policy,
            )
        finally:
            series.close()


def build(*, request_path: Path, output: Path, code_revision: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("EXACT_CODE_REVISION_REQUIRED")
    if os.environ.get("QUANTHUS_CODE_REVISION") != code_revision:
        raise ValueError("DEPLOYED_EVENT_IMAGE_REVISION_MISMATCH")
    raw = request(request_path)
    project = find_project_root(request_path)
    source = Path(raw["input_root"])
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("RETAINED_OUTPUT_SYMLINK")
    output = output.resolve()
    if output.is_relative_to(source.resolve()) or source.resolve().is_relative_to(output):
        raise ValueError("RETAINED_OUTPUT_INPUT_OVERLAP")
    if output.exists():
        # Recovery can verify an already sealed artifact, but cannot rerun failed staging.
        return verify_retained_input_artifact(
            output, code_revision=code_revision, request_digest=digest_value(raw)
        )
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    old_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(raw["budget"]["wall_seconds"])
    try:
        total = verify_inputs(source, raw)
        for member in raw["files"]:
            destination = output / member["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / member["path"], destination)
        # Verify copied bytes again before use, guarding changes during the copy.
        verify_inputs(output, raw)
        claim_attempt(Path(raw["attempt_receipt"]), output=output, revision=code_revision)
        stages = [
            {
                "stage": "verify_source_bytes",
                "elapsed_seconds": time.monotonic() - started,
                "bytes": total,
                "files": len(raw["files"]),
                "disposition": "copied_once_verified",
                "lifecycle": "immutable_private_research",
            }
        ]
        proof = _json(output, "source-lineage.json")
        s2 = _json(output, "industry/receipt.json")
        expected = raw["expected"]
        if (
            len(proof["daily_chain"]) != expected["daily_package_count"]
            or s2["source_counts"] != expected["source_counts"]
            or s2["disposition"] != "COMPLETE_TEMPORARY_STATIC_INDUSTRY_CLOSURE"
            or any(s2[name] != 0 for name in ("missing_count", "duplicate_count", "conflict_count"))
        ):
            raise ValueError("RETAINED_PREDECESSOR_EVIDENCE_DIFFERS")
        bundle = _verify_replay_bundle(output / "replay", request_digest=REQUEST_DIGEST)
        if bundle.digest != expected["bundle_digest"]:
            raise ValueError("RETAINED_BUNDLE_IDENTITY_DIFFERS")
        scope = raw["scope"]
        stage_start = time.monotonic()
        with _BoundedReader(bundle) as reader:
            connection = reader._ensure_open()
            dataset_inventory = []
            for dataset in bundle.datasets:
                paths = bundle.partition_paths(dataset.dataset)
                if len(paths) != 1:
                    raise ValueError("RETAINED_SINGLE_PARTITION_REQUIRED")
                actual = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(paths[0])]
                ).fetchone()[0]
                if (
                    actual != dataset.row_count
                    or _schema_digest(connection, paths[0]) != dataset.schema_hash
                ):
                    raise ValueError("RETAINED_DATASET_SCHEMA_OR_ROWS_DIFFER")
                dataset_inventory.append(
                    {"dataset": dataset.dataset, "rows": actual, "bytes": paths[0].stat().st_size}
                )
            calendar = calendar_audit(
                connection,
                [output / f["path"] for f in raw["files"] if f["role"] == "base_calendar"],
                [output / f["path"] for f in raw["files"] if f["role"] == "daily_calendar"],
                scope,
            )
            projection = projection_audit(
                connection, output / "candidate-daily.parquet", output / "factor.parquet"
            )
            if projection["row_count"] != expected["candidate_rows"]:
                raise ValueError("RETAINED_CANDIDATE_DENOMINATOR_DIFFERS")
            securities = {
                r[0]
                for r in connection.execute(
                    "SELECT DISTINCT dwd_security_id FROM retained_candidate"
                ).fetchall()
            }
            decision_count = count(
                connection,
                """
                SELECT count(DISTINCT dwd_security_id) FROM retained_candidate
                WHERE session_date BETWEEN DATE '2025-01-01' AND DATE '2026-09-17'""",
            )
            if (
                len(securities) != expected["candidate_securities"]
                or decision_count != expected["decision_securities"]
            ):
                raise ValueError("RETAINED_SECURITY_DENOMINATOR_DIFFERS")
            industry = load_static_industry_mapping(
                output / "industry/static_industry_mapping.json",
                expected_version="static-level1-concentration-research-v1",
                required_security_ids=securities,
            )
            ledger = coverage_ledger(connection)
            audit = replay_report(
                reader,
                date.fromisoformat(scope["decision_start"]),
                date.fromisoformat(scope["decision_end"]),
            )
            if [
                [a.isoformat(), b.isoformat()]
                for a, b in reader._date_pairs(
                    date.fromisoformat(scope["decision_start"]),
                    date.fromisoformat(scope["decision_end"]),
                )
            ] != calendar["date_pairs"]:
                raise ValueError("READER_DATE_PAIRS_DIFFER")
            spill_bytes = sum(
                p.stat().st_size
                for p in Path(reader._duckdb_temporary.name).rglob("*")
                if p.is_file()
            )
        stages.append(
            {
                "stage": "shared_retained_audit",
                "elapsed_seconds": time.monotonic() - stage_start,
                "rows": projection["row_count"],
                "bytes": total,
                "spill_bytes_at_close": spill_bytes,
                "disposition": "built_once_reused_for_calendar_projection_daily_yearly",
                "lifecycle": "TMP_removed_on_reader_close",
            }
        )
        _write(
            output / "retained-audit.json",
            {
                "calendar": calendar,
                "projection": projection,
                "coverage": ledger,
                "dataset_inventory": dataset_inventory,
                "security_count": len(securities),
                "decision_security_count": decision_count,
            },
        )
        stage_start = time.monotonic()
        target_sha = proof["target_manifest"]["sha256"]
        bridge = proof["semantic_equivalence"]
        bridge_receipt = {
            "result_digest": bridge["manifest"]["data"]["result_digest"],
            "disposition": bridge["manifest"]["data"]["disposition"],
            "original_manifest_claimed_reproduced": False,
            "original_child_binding": bridge["summary"]["data"]["missing_boundary"][
                "original_child_binding"
            ],
            "research_reconstruction": bridge["summary"]["data"]["missing_boundary"][
                "research_reconstruction"
            ],
        }
        scan = _scan(output, project, bundle, calendar, industry, target_sha, scope)
        if scan.counters.reason_counts.get("static_industry_missing", 0):
            raise ValueError("TEMPORARY_INDUSTRY_COVERAGE_INCOMPLETE")
        if not scan.candidates:
            raise ValueError("NONEMPTY_BASELINE_INPUT_REQUIRED")
        candidate_root = output / "candidate-input"
        candidate_root.mkdir()
        events = candidate_root / "events.jsonl"
        with events.open("xb") as stream:
            for item in scan.candidates:
                stream.write(
                    canonical_json_bytes(
                        {
                            "event": item.record.to_dict(),
                            "listing_code": item.listing_code,
                            "static_level1_industry_id": item.static_level1_industry_id,
                        }
                    )
                    + b"\n"
                )
                if stream.tell() > raw["budget"]["maximum_output_bytes"] - total:
                    raise InputBudgetExceeded("RETAINED_B_OUTPUT_BUDGET_EXCEEDED")
        mapping = candidate_root / "static_industry_mapping.json"
        shutil.copyfile(industry.path, mapping)
        candidate_manifest = _candidate_manifest(
            root=candidate_root,
            request_digest=REQUEST_DIGEST,
            bundle_digest=bundle.digest,
            decision_start=date.fromisoformat(scope["decision_start"]),
            decision_end=date.fromisoformat(scope["decision_end"]),
            execution_end=date.fromisoformat(scope["execution_end"]),
            events_path=events,
            industry_path=mapping,
            event_count=len(scan.candidates),
        )
        _write(candidate_root / "manifest.json", candidate_manifest)
        stages.append(
            {
                "stage": "outcome_blind_candidate_scan",
                "elapsed_seconds": time.monotonic() - stage_start,
                "rows": len(scan.candidates),
                "bytes": events.stat().st_size,
                "disposition": "reused_retained_projection_and_temporary_mapping",
                "lifecycle": "immutable_private_research",
            }
        )
        summary = {
            "schema_version": "quanthus.p3-strat-003-retained-input-summary.v1",
            "work_item": "P3-STRAT-003-B",
            "request_digest": REQUEST_DIGEST,
            "code_revision": code_revision,
            "scope": {
                "warmup_start": scope["history_start"],
                "warmup_sessions": scope["warmup_open_sessions"],
                "warmup_dates": calendar["warmup_dates"],
                **{k: scope[k] for k in ("decision_start", "decision_end", "execution_end")},
            },
            "target_release": {**proof["target_sync"]["data"], "manifest_sha256": target_sha},
            "daily_chain": proof["daily_chain"],
            "semantic_equivalence_prerequisite": bridge_receipt,
            "prior_chain_recovery": {
                "mode": "retained_source_lineage_receipt",
                "rebuilt_package_count": 0,
                "original_execution_reproduced": False,
            },
            "deferred_daily_artifact_contract": {
                "successful_ready_package_requires_deferred_daily_artifacts_empty": True,
                "daily_package_count": len(proof["daily_chain"]),
                "evidence_kind": "original_release_contract_not_new_independent_measurement",
            },
            "replay_audit": audit.to_dict(),
            "candidate_scan": {
                "evaluated_rows": scan.counters.evaluated_rows,
                "raw_triggers": scan.counters.raw_triggers,
                "isolated_candidates": scan.counters.isolated_candidates,
                "concentration_retained": scan.counters.concentration_retained,
                "candidate_events": scan.counters.candidate_events,
                "reason_counts": dict(sorted(scan.counters.reason_counts.items())),
            },
            "bundle_digest": bundle.digest,
            "candidate_manifest_digest": candidate_manifest["semantic_digest"],
            "retained_gates_passed": True,
            "temporary_research_only": True,
            "formal_DWD_repair": False,
            "data_issues_closed": [],
            "B_accepted": False,
            "D_activated": False,
            "strategy_or_economic_result_count": 0,
            "outcome_label_count": 0,
            "p2_probe_count": 0,
            "p2_fill_count": 0,
            "p6_mutation": False,
            "stage_graph": raw["stage_graph"],
            "stages": stages,
            "elapsed_seconds": time.monotonic() - started,
        }
        _write(output / "summary.json", summary)
        files = {
            p.relative_to(output).as_posix(): {
                "path": p.relative_to(output).as_posix(),
                "sha256": _sha256(p),
                "size_bytes": p.stat().st_size,
            }
            for p in sorted(output.rglob("*"))
            if p.is_file()
        }
        if sum(f["size_bytes"] for f in files.values()) > raw["budget"]["maximum_output_bytes"]:
            raise InputBudgetExceeded("RETAINED_B_OUTPUT_BUDGET_EXCEEDED")
        # Existing consumer contract keeps its canonical candidate-manifest role.
        files["candidate_manifest"] = files.pop("candidate-input/manifest.json")
        manifest = {
            "schema_version": ARTIFACT_SCHEMA,
            "work_item": "P3-STRAT-003-B",
            "request_digest": digest_value(raw),
            "frozen_research_request_digest": REQUEST_DIGEST,
            "code_revision": code_revision,
            "partition": PARTITION,
            "scope": summary["scope"],
            "qualification_evidence": _qualification_evidence(summary),
            "files": files,
            "retained_source_request_sha256": REQUEST_SHA,
            "semantic_equivalence_result_digest": bridge_receipt["result_digest"],
            "bundle_digest": bundle.digest,
            "candidate_manifest_digest": candidate_manifest["semantic_digest"],
            "row_count": audit.decision_observation_count,
            "candidate_event_count": scan.counters.candidate_events,
            "execution_authorized": False,
            "strategy_result_produced": False,
            "outcome_labels_produced": False,
            "p6_mutation": False,
        }
        _write(output / "manifest.json", {**manifest, "result_digest": digest_value(manifest)})
        (output / "READY").write_text(_sha256(output / "manifest.json") + "\n", encoding="ascii")
        return verify_retained_input_artifact(
            output, code_revision=code_revision, request_digest=digest_value(raw)
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def verify_retained_input_artifact(
    root: Path,
    *,
    code_revision: str,
    request_digest: str,
) -> dict[str, Any]:
    raw = request(find_project_root() / REQUEST_CONFIG)
    if digest_value(raw) != request_digest:
        raise ValueError("RETAINED_REQUEST_DIGEST_DIFFERS")
    verify_inputs(root, raw)
    result = verify_p3_strat_003_input_artifact(
        root, code_revision=code_revision, request_digest=request_digest
    )
    manifest, summary, audit = (
        _json(root, name) for name in ("manifest.json", "summary.json", "retained-audit.json")
    )
    declared = {v["path"] for v in manifest["files"].values()}
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if declared != actual - {"manifest.json", "READY"}:
        raise ValueError("RETAINED_ARTIFACT_FILE_CLOSURE_DIFFERS")
    source = _json(root, "source-lineage.json")
    candidate = _json(root, "candidate-input/manifest.json")
    calendar = audit["calendar"]
    expected_scope = {
        "warmup_start": raw["scope"]["history_start"],
        "warmup_sessions": raw["scope"]["warmup_open_sessions"],
        "warmup_dates": calendar["warmup_dates"],
        **{k: raw["scope"][k] for k in ("decision_start", "decision_end", "execution_end")},
    }
    expected_target = {
        **source["target_sync"]["data"],
        "manifest_sha256": source["target_manifest"]["sha256"],
    }
    counts = summary["replay_audit"]["counts"]
    if (
        _sha256(root / "candidate-input/static_industry_mapping.json")
        != _sha256(root / "industry/static_industry_mapping.json")
        or summary["target_release"] != expected_target
        or summary["daily_chain"] != source["daily_chain"]
        or summary["scope"] != expected_scope
        or manifest["scope"] != expected_scope
        or manifest["semantic_equivalence_result_digest"]
        != source["semantic_equivalence"]["manifest"]["data"]["result_digest"]
        or summary["candidate_scan"]["candidate_events"] != candidate["events"]["count"]
        or manifest["candidate_event_count"] != candidate["events"]["count"]
        or manifest["row_count"] != counts["decision_observations"]
        or counts["frames"] != len(calendar["date_pairs"])
        or audit["security_count"] != raw["expected"]["candidate_securities"]
        or audit["decision_security_count"] != raw["expected"]["decision_securities"]
        or len(calendar["warmup_dates"]) != raw["scope"]["warmup_open_sessions"]
        or calendar["digest"] != digest_value({k: v for k, v in calendar.items() if k != "digest"})
        or sum(
            r["decision_universe_pairs"]
            for r in audit["coverage"]["decision_execution_denominators"]
        )
        != counts["decision_observations"]
    ):
        raise ValueError("RETAINED_EVIDENCE_CROSS_BINDING_DIFFERS")
    if (
        manifest.get("retained_source_request_sha256") != REQUEST_SHA
        or manifest.get("execution_authorized") is not False
        or summary.get("retained_gates_passed") is not True
        or summary.get("B_accepted") is not False
        or summary.get("D_activated") is not False
        or summary.get("formal_DWD_repair") is not False
        or summary.get("data_issues_closed") != []
        or audit["calendar"]["external_calendar_set_equal"] is not True
        or audit["calendar"]["warmup_set_equal"] is not True
        or any(audit["projection"]["mismatch_counts"].values())
        or audit["projection"]["row_count"] != raw["expected"]["candidate_rows"]
        or result["candidate_event_count"] <= 0
    ):
        raise ValueError("RETAINED_ARTIFACT_GATES_DIFFER")
    return {
        **result,
        "retained_input_requalified": True,
        "B_accepted": False,
        "temporary_research_only": True,
        "D_activated": False,
        "formal_DWD_repair": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build(request_path=args.request, output=args.output, code_revision=args.code_revision),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
