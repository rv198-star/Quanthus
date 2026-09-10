"""Fixed real-input reference feasibility; never fetch DWD, infer opinions or search rules."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_reference_protocol import (
    PROTOCOL_DIGEST,
    _decode_rows,
    _no_links,
    _unique_object,
    build_evidence,
    publish_bundle,
    resolve_opinions,
    reviewer_view,
    validate_evidence,
    verify_bundle,
)
from quanthus.structure_f9_artifact import verify_structure_f9_artifact
from quanthus.structure_order_artifact import _row, load_structure_order_qualification
from quanthus.structure_v2_inputs import DailyInput, InputPolicy, canonical, digest

CONFIG = "config/stage-learning/order-reference-feasibility.v1.json"
CONFIG_DIGEST = "sha256:f161f1931c724df05b6dff0851185fe9e8571286841b3fd06e013a3821fdaf22"
SCHEMA = "quanthus.stage-order-reference-feasibility-artifact.v1"
MAX_BYTES = 33554432
RUN_RE = re.compile(r"run_[0-9a-f]{32}")
REV_RE = re.compile(r"[0-9a-f]{40}")
SHA_RE = re.compile(r"sha256:[0-9a-f]{64}")
PREP_FILES = ("evidence.json", "source-inputs.jsonl", "source-binding.json", "runtime.json")
FINAL_FILES = ("feasibility.json", "parent.json", "runtime.json")


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_config() -> dict:
    config = json.loads((find_project_root() / CONFIG).read_bytes())
    if digest(config) != CONFIG_DIGEST or config["protocol_digest"] != PROTOCOL_DIGEST:
        raise ValueError("reference_feasibility_config_drift")
    return config


def source_contract() -> dict:
    return load_structure_order_qualification(
        find_project_root() / load_config()["source_contract"]
    )["source_f9"]


def normalize_input(value: Mapping[str, Any]) -> dict:
    """Only two bounded phases; no file paths, arbitrary configs or reviewer impersonation."""
    config = load_config()
    base = {"code_revision", "run_config", "phase"}
    phase = value.get("phase")
    if phase == "resolve":
        base |= {"prepared_run_id", "prepared_manifest_sha256", "opinions"}
    if (phase not in {"prepare", "resolve"} or set(value) != base
            or value.get("run_config") != CONFIG
            or not isinstance(value.get("code_revision"), str)
            or not REV_RE.fullmatch(value["code_revision"])):
        raise ValueError("reference_feasibility_input_contract")
    if len(canonical(dict(value))) > config["bounds"]["max_input_bytes"]:
        raise ValueError("reference_feasibility_input_budget")
    if phase == "resolve":
        if (not isinstance(value["prepared_run_id"], str)
                or not RUN_RE.fullmatch(value["prepared_run_id"])
                or not isinstance(value["prepared_manifest_sha256"], str)
                or not SHA_RE.fullmatch(value["prepared_manifest_sha256"])):
            raise ValueError("reference_feasibility_parent_identity")
        opinions = value["opinions"]
        if not isinstance(opinions, list) or len(opinions) != 24:
            raise ValueError("reference_feasibility_requires_24_opinions")
        expected = {key: config["review"][key] for key in ("kind", "origin_id", "session_id")}
        for opinion in opinions:
            if not isinstance(opinion, dict) or opinion.get("provenance") != expected:
                raise ValueError("reference_feasibility_actual_review_provenance")
    return json.loads(canonical(dict(value)))


def event_root() -> Path:
    return Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs"))


def select_cases(rows: Sequence[DailyInput]) -> list[tuple[str, date]]:
    config = load_config()["selection"]
    first, last = (date.fromisoformat(config[k]) for k in ("report_start", "report_end"))
    pairs = [(row.security_code, row.market_date) for row in rows]
    if len(pairs) != len(set(pairs)):
        raise ValueError("reference_source_duplicate_sessions")
    if {code for code, _ in pairs} != set(config["security_codes"]):
        raise ValueError("reference_source_security_scope")
    result = []
    for code in config["security_codes"]:
        days = sorted(day for security, day in pairs if security == code and first <= day <= last)
        if len(days) < 4:
            raise ValueError("reference_fixed_selection_insufficient_no_replacement")
        result.extend((code, days[j * (len(days) - 1) // 3]) for j in range(4))
    return result


def _read(path: Path) -> bytes:
    _no_links(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("reference_regular_bounded_file_required")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("reference_file_budget")
    return raw


def _json(data: bytes) -> Any:
    return json.loads(data, object_pairs_hook=_unique_object)


def _source_rows(raw: bytes) -> list[DailyInput]:
    rows = [_row(_json(line)) for line in raw.splitlines() if line.strip()]
    if len(rows) != 357:
        raise ValueError("reference_frozen_source_row_count")
    if any(not date(2019, 1, 1) <= row.market_date <= date(2020, 3, 31) for row in rows):
        raise ValueError("reference_source_partition_violation")
    return rows


def _check_source_binding(raw: bytes, binding: dict) -> str:
    source = source_contract()
    if set(binding) != {"source_contract", "manifests", "readiness"}:
        raise ValueError("reference_source_binding_fields")
    if binding["source_contract"] != source or sha(raw) != source["source_inputs_digest"]:
        raise ValueError("reference_accepted_source_identity")
    manifests = binding["manifests"]
    if not isinstance(manifests, list) or len(manifests) != 2:
        raise ValueError("reference_two_source_manifests_required")
    for idx, manifest in enumerate(manifests):
        if (digest(manifest) != source["manifest_sha256"][idx]
                or manifest["files"]["source-inputs.jsonl"] != sha(raw)
                or manifest["files"]["input-readiness.json"] != digest(binding["readiness"])):
            raise ValueError("reference_source_manifest_membership")
    return binding["readiness"]["calendar_ref"]


def read_accepted_sources() -> tuple[bytes, dict]:
    source = source_contract()
    raw_sources, manifests, readiness = [], [], []
    for run_id, expected in zip(source["run_ids"], source["manifest_sha256"], strict=True):
        path = event_root() / "structure-runs" / run_id
        _no_links(path)
        observed = verify_structure_f9_artifact(
            path, code_revision=source["code_revision"], request_digest=source["request_digest"],
        )
        if observed["manifest_sha256"] != expected:
            raise ValueError("reference_source_deep_verifier_identity")
        raw_sources.append(_read(path / "source-inputs.jsonl"))
        manifests.append(_json(_read(path / "manifest.json")))
        readiness.append(_json(_read(path / "input-readiness.json")))
    if raw_sources[0] != raw_sources[1] or canonical(readiness[0]) != canonical(readiness[1]):
        raise ValueError("reference_source_ab_mismatch")
    binding = {"source_contract": source, "manifests": manifests, "readiness": readiness[0]}
    _check_source_binding(raw_sources[0], binding)
    return raw_sources[0], binding


def _seal(target: Path, inputs: dict, payloads: dict[str, bytes]) -> None:
    files = dict(payloads)
    if inputs["phase"] == "resolve":
        for path in sorted((target / "bundle").iterdir()):
            files["bundle/" + path.name] = _read(path)
    if sum(map(len, files.values())) > MAX_BYTES - 100000:
        raise ValueError("reference_feasibility_artifact_budget")
    manifest = {
        "schema_version": SCHEMA, "config_digest": CONFIG_DIGEST,
        "code_revision": inputs["code_revision"], "job_input": inputs,
        "job_input_digest": digest(inputs),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: sha(data) for name, data in sorted(files.items())},
    }
    for name, raw in payloads.items():
        with (target / name).open("xb") as stream:
            stream.write(raw)
    manifest_raw = canonical(manifest)
    with (target / "manifest.json").open("xb") as stream:
        stream.write(manifest_raw)
    with (target / "READY").open("x", encoding="ascii") as stream:
        stream.write(sha(manifest_raw) + "\n")


def prepare(target: Path, inputs: dict) -> None:
    started = time.perf_counter()
    raw, binding = read_accepted_sources()
    source_seconds = time.perf_counter() - started
    rows = _source_rows(raw)
    keys = select_cases(rows)
    policy = InputPolicy(PROTOCOL_DIGEST, tuple(load_config()["selection"]["security_codes"]),
                         binding["readiness"]["calendar_ref"])
    stages: list[dict] = []
    evidence = build_evidence(
        rows, keys, policy, code_revision=inputs["code_revision"],
        source_kind="retrospective_development", progress=stages.append,
    )
    target.mkdir(parents=True, exist_ok=False)
    _seal(target, inputs, {
        "source-inputs.jsonl": raw, "source-binding.json": canonical(binding),
        "evidence.json": canonical(evidence),
        "runtime.json": canonical({"source_verification_seconds": source_seconds,
                                   "stages": stages, "source_artifact_reads": 2,
                                   "unique_source_rows": len(rows), "dwd_provider_opens": 0,
                                   "elapsed_seconds": time.perf_counter() - started}),
    })


def feasibility_summary(summary: dict) -> dict:
    counts = summary["primary_reference_counts"]
    covered = (counts.get("STAGE_0", 0) > 0 and counts.get("STAGE_2", 0) > 0
               and counts.get("RULE_CONFLICT", 0) == 0)
    return {**summary, "collection_disposition": "PASS_REAL_INPUT_REFERENCE_COLLECTION",
            "coverage_disposition": ("DESCRIPTIVE_REFERENCE_COVERAGE_OBSERVED" if covered
                                     else "INCONCLUSIVE_REFERENCE_COVERAGE_NO_SUPPLEMENT"),
            "full_context_blinding_claimed": False,
            "new_parameter_candidate_accepted": False, "market_representativeness": False}


def resolve(target: Path, inputs: dict) -> None:
    started = time.perf_counter()
    parent = event_root() / "structure-runs" / inputs["prepared_run_id"]
    if _json(_read(parent / "manifest.json")).get("job_input", {}).get("phase") != "prepare":
        raise ValueError("reference_parent_must_be_prepare")
    receipt = verify_feasibility_artifact(
        parent, code_revision=inputs["code_revision"], request_digest=CONFIG_DIGEST,
        include_views=False,
    )
    if receipt["phase"] != "prepare" or (
        receipt["manifest_sha256"] != inputs["prepared_manifest_sha256"]
    ):
        raise ValueError("reference_prepare_parent_mismatch")
    evidence = _json(_read(parent / "evidence.json"))
    # Validator, not an opinion generator: all opinions must arrive from the actual reviewer.
    _, summary = resolve_opinions(evidence, inputs["opinions"])
    target.mkdir(parents=True, exist_ok=False)
    publish_bundle(target / "bundle", evidence, inputs["opinions"])
    _seal(target, inputs, {
        "parent.json": canonical({"run_id": inputs["prepared_run_id"],
                                  "manifest_sha256": inputs["prepared_manifest_sha256"]}),
        "feasibility.json": canonical(feasibility_summary(summary)),
        "runtime.json": canonical({"dwd_provider_opens": 0, "new_fact_materializations": 0,
                                   "mode": "reuse_prepared_evidence_plus_explicit_audits",
                                   "elapsed_seconds": time.perf_counter() - started}),
    })


def _views(evidence: dict) -> list[dict]:
    output = []
    for case in evidence["cases"]:
        view = reviewer_view(evidence, case["case_id"])
        output.append({"case": case, "history_columns": ["date", "high", "low", "close"],
                       "history": [[row[k] for k in ("market_date", "high", "low", "close")]
                                   for row in view["price_history"]]})
    return output


def verify_feasibility_artifact(
    root: Path, *, code_revision: str, request_digest: str,
    job_input: Mapping[str, Any] | None = None, include_views: bool = True,
) -> dict:
    """Cold verification binds retained raw sources, exact selection, evidence and raw opinions."""
    _no_links(root)
    raw_manifest = _read(root / "manifest.json")
    manifest = _json(raw_manifest)
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "config_digest", "code_revision", "job_input",
        "job_input_digest", "generated_at", "files",
    }:
        raise ValueError("reference_feasibility_manifest_fields")
    clock = datetime.fromisoformat(manifest["generated_at"])
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("reference_feasibility_manifest_clock")
    if (manifest.get("schema_version") != SCHEMA or request_digest != CONFIG_DIGEST
            or manifest.get("config_digest") != CONFIG_DIGEST
            or manifest.get("code_revision") != code_revision
            or _read(root / "READY").decode("ascii").strip() != sha(raw_manifest)):
        raise ValueError("reference_feasibility_manifest_identity")
    inputs = normalize_input(manifest["job_input"])
    if (inputs["code_revision"] != code_revision
            or manifest.get("job_input_digest") != digest(inputs)
            or (job_input is not None
                and canonical(inputs) != canonical(normalize_input(job_input)))):
        raise ValueError("reference_feasibility_job_input_binding")
    phase = inputs["phase"]
    expected = set(PREP_FILES if phase == "prepare" else FINAL_FILES)
    if phase == "resolve":
        expected |= {"bundle/" + name for name in (
            "evidence.json", "reviews.json", "resolved.json", "summary.json",
            "manifest.json", "READY",
        )}
    allowed_dirs = {root / "bundle"} if phase == "resolve" else set()
    if {path for path in root.rglob("*") if path.is_dir()} != allowed_dirs:
        raise ValueError("reference_feasibility_unexpected_directory")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if not path.is_dir()}
    if actual != expected | {"manifest.json", "READY"} or set(manifest["files"]) != expected:
        raise ValueError("reference_feasibility_inventory")
    consumed = 0
    for name in sorted(expected):
        data = _read(root / name)
        consumed += len(data)
        if consumed > MAX_BYTES or sha(data) != manifest["files"][name]:
            raise ValueError("reference_feasibility_file_digest_or_budget")
    result = {"artifact_ref": str(root), "manifest_sha256": sha(raw_manifest),
              "phase": phase, "verified": True, "row_count": 12, "p2_probe_count": 0,
              "p2_fill_count": 0, "dwd_provider_opens": 0, "execution_authorized": False,
              "official_label_card_authorized": False,
              "runtime": _json(_read(root / "runtime.json"))}
    if phase == "prepare":
        raw = _read(root / "source-inputs.jsonl")
        binding = _json(_read(root / "source-binding.json"))
        calendar = _check_source_binding(raw, binding)
        raw_rows = _source_rows(raw)
        evidence = _json(_read(root / "evidence.json"))
        validate_evidence(evidence)
        stored_rows, policy = _decode_rows(evidence)
        normalized = {digest(row): row for row in _normalized_source(raw_rows)}
        if ({digest(row): row for row in stored_rows} != normalized
                or policy.calendar_ref != calendar):
            raise ValueError("reference_evidence_source_membership")
        selected = select_cases(raw_rows)
        if ([(c["security_code"], date.fromisoformat(c["market_date"])) for c in evidence["cases"]]
                != selected or evidence["code_revision"] != code_revision
                or evidence["source_kind"] != "retrospective_development"
                or any(c["sampling_stratum"] != "unspecified" for c in evidence["cases"])):
            raise ValueError("reference_selection_drift")
        result.update({"evidence_digest": digest(evidence), "case_count": len(selected),
                       "source_inputs_digest": sha(raw), "source_row_count": len(raw_rows)})
        if include_views:
            result["review_packets"] = _views(evidence)
    else:
        summary = verify_bundle(root / "bundle")
        parent = event_root() / "structure-runs" / inputs["prepared_run_id"]
        if _json(_read(parent / "manifest.json")).get("job_input", {}).get("phase") != "prepare":
            raise ValueError("reference_parent_must_be_prepare_no_recursive_resolution")
        prior = verify_feasibility_artifact(
            parent, code_revision=code_revision, request_digest=CONFIG_DIGEST, include_views=False,
        )
        evidence = _json(_read(root / "bundle/evidence.json"))
        reviews = _json(_read(root / "bundle/reviews.json"))
        if (prior["phase"] != "prepare"
                or prior["manifest_sha256"] != inputs["prepared_manifest_sha256"]
                or digest(evidence) != prior["evidence_digest"]
                or sorted(reviews, key=lambda r: (r["case_id"], r["role"])) != sorted(
                    inputs["opinions"], key=lambda r: (r["case_id"], r["role"]))
                or _json(_read(root / "parent.json")) != {
                    "run_id": inputs["prepared_run_id"],
                    "manifest_sha256": inputs["prepared_manifest_sha256"]}):
            raise ValueError("reference_resolution_parent_or_opinion_binding")
        _, expected_summary = resolve_opinions(evidence, reviews)
        final = _json(_read(root / "feasibility.json"))
        if final != feasibility_summary(expected_summary):
            raise ValueError("reference_feasibility_result_reconstruction")
        result.update(final)
        result["bundle_manifest_sha256"] = summary["manifest_sha256"]
        result["opinion_digest"] = digest(reviews)
        result["parent_manifest_sha256"] = prior["manifest_sha256"]
    return result


def _normalized_source(rows: Sequence[DailyInput]) -> list[DailyInput]:
    from dataclasses import replace
    return [replace(row, cutoff=row.result_as_of) for row in rows]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--job-input", required=True)
    args = parser.parse_args(argv)
    inputs = normalize_input(_json(args.job_input.encode()))
    if args.request != CONFIG or args.code_revision != inputs["code_revision"]:
        raise ValueError("reference_feasibility_cli_identity")
    _no_links(args.output)
    if args.output.exists():
        raise ValueError("reference_feasibility_immutable_target_exists")
    def deadline(*_: Any) -> None:
        raise TimeoutError("reference_feasibility_wall_budget")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(180)
    (prepare if inputs["phase"] == "prepare" else resolve)(args.output, inputs)
    print(json.dumps({"phase": inputs["phase"], "sealed": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
