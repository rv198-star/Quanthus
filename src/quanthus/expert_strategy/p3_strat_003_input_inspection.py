"""Bounded metadata attribution for one failed P3-STRAT-003-B input Job."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import time
from datetime import date, datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any, NoReturn

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)
from quanthus.expert_strategy.p3_strat_003_input_qualification import (
    _request,
    _semantic_equivalence_prerequisite,
)
from quanthus.forward_validation.activation import load_prospective_runtime_config
from quanthus.policy import find_project_root

REQUEST_DIGEST = "sha256:54cf8d8d0a5ab8f5e67c4c91aa830e09e92e03a9180d856d009a0d08a140f168"
ARTIFACT_SCHEMA = "quanthus.p3-strat-003-input-inspection-artifact.v1"
DISPOSITION = "INPUT_IDENTITY_INSPECTION_COMPLETE"
COUNTS = dict.fromkeys(("row_count", "p2_probe_count", "p2_fill_count", "candidate_event_count"), 0)
FLAGS = dict.fromkeys(
    ("execution_authorized", "strategy_result_produced", "outcome_labels_produced",
     "p6_mutation", "daily_current_mutation", "b_acceptance"), False
)
_SHA = r"sha256:[0-9a-f]{64}"
_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}"
_FRAMES = {
    "main", "build_input_artifact", "_load_bound_daily_chain", "_fetch_bound_prior_package",
    "_reconstruct_exact_prior_package", "_build_reconstruction_suffix",
}


def _fail(detail: str) -> NoReturn:
    raise ExpertEvaluationError("p3_strat_003_input_inspection_invalid", detail)


def _budget(deadline: float) -> None:
    if time.monotonic() > deadline:
        _fail("inspection wall-clock limit exceeded")


def _read(path: Path, *, limit: int, root: Path, deadline: float) -> bytes:
    _budget(deadline)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        _fail("metadata path escapes its fixed read root")
    try:
        with path.open("rb") as source:
            data = source.read(limit + 1)
    except OSError as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_inspection_unreadable", "fixed metadata file is unreadable"
        ) from exc
    if len(data) > limit:
        _fail("metadata byte limit exceeded")
    _budget(deadline)
    return data


def _object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ExpertEvaluationError(
            "p3_strat_003_input_inspection_invalid", "metadata JSON is invalid"
        ) from exc
    if not isinstance(value, dict):
        _fail("metadata must be an object")
    return value


def _text(value: Any, pattern: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        _fail("metadata identity has an invalid shape")
    return value


def _optional_text(value: Any, pattern: str) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _identity(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "market_date": _text(raw.get("market_date"), r"\d{4}-\d{2}-\d{2}"),
        "package_id": _text(raw.get("package_id"), _ID),
        "manifest_sha256": _text(raw.get("manifest_sha256"), _SHA),
    }


def _metadata_record(
    data: bytes, *, path: Path, root: Path, source: str
) -> dict[str, Any]:
    raw = _object(data)
    if (raw.get("schema_version") != 1
            or raw.get("kind") != "cn01_daily_dwd_domain_increment"):
        _fail("daily manifest schema differs")
    identity = _identity({
        **raw, "manifest_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    })
    prior = raw.get("prior")
    if prior is not None and not isinstance(prior, dict):
        _fail("daily prior identity is not an object")
    return {
        **identity, "source": source, "relative_path": path.relative_to(root).as_posix(),
        "manifest_size_bytes": len(data),
        "covered_after_date": _optional_text(raw.get("covered_after_date"), r"\d{4}-\d{2}-\d{2}"),
        "code_revision": _optional_text(raw.get("code_revision"), r"[0-9a-f]{7,40}"),
        "prior": _identity(prior) if prior is not None else None,
    }


def _scan(root: Path, *, limits: dict[str, Any], deadline: float) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    directories = 0
    for offset in range((date(2026, 9, 18) - date(2026, 8, 1)).days + 1):
        market_date = (date(2026, 8, 1) + timedelta(days=offset)).isoformat()
        folder = root / "packages" / market_date
        _budget(deadline)
        if not folder.exists():
            continue
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            _fail("daily metadata directory escapes its read root")
        children = list(islice(folder.iterdir(), limits["maximum_packages"] + 1))
        directories += len(children)
        if directories > limits["maximum_packages"]:
            _fail("daily package inventory exceeds the package limit")
        for package in sorted(children):
            if not package.is_dir():
                continue
            _text(package.name, _ID)
            if package.is_symlink():
                _fail("daily package directory is a symlink")
            path = package / "manifest.json"
            if not path.exists():
                continue
            data = _read(path, limit=limits["maximum_manifest_bytes"], root=root, deadline=deadline)
            record = _metadata_record(
                data, path=path, root=root, source="retained_daily_inventory",
            )
            if record["market_date"] != market_date:
                _fail("daily manifest identity disagrees with its date directory")
            records.append(record)
    return records


def _stderr(root: Path, failed: dict[str, Any], *, limit: int, deadline: float) -> dict[str, Any]:
    path = root / ".staging" / failed["job_id"] / str(failed["claim_generation"]) / "stderr.log"
    if not path.exists():
        return {"status": "missing"}
    try:
        data = _read(path, limit=limit, root=root, deadline=deadline)
    except ExpertEvaluationError:
        return {"status": "unavailable_within_metadata_limits"}
    text = data.decode("utf-8", errors="replace")
    frames = re.findall(
        r'File "[^"\n]*/p3_strat_003_input_qualification\.py", line \d+, in ([a-z_]+)', text
    )
    prefixes = failed["observed_manifest_sha256_prefixes"]
    known = [failed["expected_manifest_sha256"], *prefixes]
    full_hashes = list(dict.fromkeys(re.findall(r"sha256:[0-9a-f]{64}(?![0-9a-f])", text)))
    return {
        "status": "read_whitelist_only", "size_bytes": len(data),
        "file_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "frame_names": list(dict.fromkeys(name for name in frames if name in _FRAMES)),
        "declared_error_code_present": failed["error_code"] in text,
        "declared_hash_or_prefix_mentions": [value for value in known if value in text],
        "observed_full_hashes_matching_declared_prefixes": [
            value for value in full_hashes if any(value.startswith(prefix) for prefix in prefixes)
        ],
    }


def build_input_inspection(
    *, request_path: Path, output: Path, code_revision: str
) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    _text(code_revision, r"[0-9a-f]{40}")
    project = find_project_root(request_path.resolve())
    raw = _object(_read(request_path, limit=262144, root=project, deadline=deadline))
    if digest_value(raw) != REQUEST_DIGEST:
        _fail("inspection request differs from the frozen metadata-only contract")
    qualification_path = project / raw["qualification"]["path"]
    data = _read(qualification_path, limit=262144, root=project, deadline=deadline)
    if "sha256:" + hashlib.sha256(data).hexdigest() != raw["qualification"]["file_sha256"]:
        _fail("bound B-v2 qualification request differs")
    qualification = _request(qualification_path)
    probe_root = Path(qualification["semantic_equivalence_prerequisite"]["artifact_ref"]).resolve()
    probe_summary = _object(_read(
        probe_root / "summary.json", limit=262144, root=probe_root, deadline=deadline
    ))
    _read(probe_root / "manifest.json", limit=262144, root=probe_root, deadline=deadline)
    prerequisite = _semantic_equivalence_prerequisite(qualification)
    if prerequisite is None:
        _fail("the sealed B-v2 semantic prerequisite is missing")
    _budget(deadline)
    runtime = load_prospective_runtime_config(
        project / qualification["source_runtime"]["path"], root=project,
    )
    output = output.resolve()
    daily_root = runtime.daily_root.resolve()
    event_root = prerequisite.artifact_root.parent.parent.resolve()
    failed_root = event_root / ".staging" / raw["failed_job"]["job_id"]
    for source in (daily_root, runtime.historical_base_root, runtime.correction_root,
                   prerequisite.artifact_root, failed_root, project):
        source = source.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            _fail("inspection output overlaps a read-only input root")
    if output.exists():
        return verify_input_inspection_artifact(
            output, code_revision=code_revision, request_digest=REQUEST_DIGEST,
        )
    records = _scan(daily_root, limits=raw["limits"], deadline=deadline)
    research_path = prerequisite.research_package_root / "manifest.json"
    research = _metadata_record(
        _read(research_path, limit=262144, root=probe_root, deadline=deadline),
        path=research_path, root=probe_root, source="sealed_probe_research_package",
    )
    if (research["manifest_sha256"] != prerequisite.research_manifest_sha256
            or research["package_id"] != prerequisite.research_package_id
            or research["market_date"] != prerequisite.boundary_market_date.isoformat()):
        _fail("sealed research metadata changed after prerequisite verification")
    expected = raw["failed_job"]["expected_manifest_sha256"]
    matches = [
        {"relation": relation, **record}
        for record in [*records, research]
        for relation, identity in (("manifest", record), ("prior", record["prior"]))
        if identity is not None and identity["manifest_sha256"] == expected
    ]
    matched_identities = {
        tuple((item["prior"] if item["relation"] == "prior" else item)[key]
              for key in ("market_date", "package_id", "manifest_sha256"))
        for item in matches
    }
    boundary = probe_summary["missing_boundary"]
    core = {
        "failed_job": raw["failed_job"], "sealed_probe": prerequisite.receipt(),
        "probe_anchor": {
            "market_date": _text(boundary["anchor_market_date"], r"\d{4}-\d{2}-\d{2}"),
            "package_id": _text(boundary["anchor_package_id"], _ID),
        },
        "daily_root": str(daily_root), "observed_manifest_count": len(records),
        "sealed_probe_research_package": research, "expected_hash_matches": matches,
        "match_occurrence_count": len(matches), "distinct_identity_count": len(matched_identities),
        "match_disposition_basis": "matching_occurrences",
        "match_disposition": (
            "none" if not matches else "unique" if len(matches) == 1 else "multiple"
        ),
        "inventory_observed_at": datetime.now(timezone.utc).isoformat(),
        "claim_failed_job_inventory_reproduced": False, "retained_daily_payloads_read": False,
        "stderr": _stderr(
            event_root, raw["failed_job"],
            limit=raw["limits"]["maximum_stderr_bytes"], deadline=deadline,
        ),
    }
    summary = {"inspection": core, "daily_manifest_inventory": records}
    summary_data = canonical_json_bytes(summary) + b"\n"
    payload = {
        "schema_version": ARTIFACT_SCHEMA, "work_item": "P3-STRAT-003-B",
        "code_revision": code_revision, "request_digest": REQUEST_DIGEST,
        "disposition": DISPOSITION, **COUNTS, **FLAGS, "inspection": core,
        "files": {"summary": {"path": "summary.json", "size_bytes": len(summary_data),
                             "sha256": "sha256:" + hashlib.sha256(summary_data).hexdigest()}},
    }
    manifest_data = (
        canonical_json_bytes({**payload, "result_digest": digest_value(payload)}) + b"\n"
    )
    ready = ("sha256:" + hashlib.sha256(manifest_data).hexdigest() + "\n").encode("ascii")
    if len(summary_data) + len(manifest_data) + len(ready) > raw["limits"]["maximum_output_bytes"]:
        _fail("complete inspection artifact exceeds the output limit")
    _budget(deadline)
    output.mkdir(parents=True)
    (output / "summary.json").write_bytes(summary_data)
    (output / "manifest.json").write_bytes(manifest_data)
    (output / "READY").write_bytes(ready)
    return verify_input_inspection_artifact(
        output, code_revision=code_revision, request_digest=REQUEST_DIGEST,
    )


def verify_input_inspection_artifact(
    root: Path, *, code_revision: str, request_digest: str
) -> dict[str, Any]:
    root = root.resolve()
    deadline = time.monotonic() + 60
    data = _read(root / "manifest.json", limit=131072, root=root, deadline=deadline)
    summary_data = _read(root / "summary.json", limit=131072, root=root, deadline=deadline)
    ready = _read(root / "READY", limit=128, root=root, deadline=deadline)
    manifest, summary = _object(data), _object(summary_data)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    payload = {key: value for key, value in manifest.items() if key != "result_digest"}
    expected_file = {"summary": {"path": "summary.json", "size_bytes": len(summary_data),
                                 "sha256": "sha256:" + hashlib.sha256(summary_data).hexdigest()}}
    if (len(data) + len(summary_data) + len(ready) > 131072
            or ready != (digest + "\n").encode("ascii")
            or manifest.get("schema_version") != ARTIFACT_SCHEMA
            or manifest.get("work_item") != "P3-STRAT-003-B"
            or manifest.get("code_revision") != code_revision
            or request_digest != REQUEST_DIGEST
            or manifest.get("request_digest") != request_digest
            or manifest.get("disposition") != DISPOSITION
            or manifest.get("result_digest") != digest_value(payload)
            or manifest.get("files") != expected_file
            or manifest.get("inspection") != summary.get("inspection")
            or any(manifest.get(key) is not False for key in FLAGS)
            or any(manifest.get(key) != 0 for key in COUNTS)):
        _fail("sealed inspection artifact identity or boundary differs")
    return {
        "artifact_ref": str(root), "manifest_sha256": digest,
        "result_digest": manifest["result_digest"], "request_digest": request_digest,
        "disposition": DISPOSITION, **COUNTS, **FLAGS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect fixed P3-STRAT-003-B input metadata")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    args = parser.parse_args(argv)
    signal.alarm(60)
    result = build_input_inspection(
        request_path=args.request, output=args.output, code_revision=args.code_revision,
    )
    signal.alarm(0)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
