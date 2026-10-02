"""Compose the approved temporary static source; never runs B candidates or strategies."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.event_library.physical_scan import _read_parquet_expression, _table_paths
from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy.p3_strat_003_industry_inspection import (
    REQUEST_DIGEST,
    _candidate_inventory,
    _dwd_reference,
    _p4_reference,
    _sha256,
    _verify_dwd_industry_source,
)
from quanthus.industry_mapping import load_static_industry_mapping
from quanthus.p3_temporary_industry import checked_json, qualify, validate_inputs, write_new

REQUEST_SHA = "sha256:b4f44ccd09be346b4bd13d1fb978d6ec7b124535e4ea4a8b1aae777834b28c27"
RUNTIME_ROOT = Path("/home/ubuntu/quanthus-research/p3-p5-20261002/p3-temporary-industry-closure")


def validate_private_root() -> None:
    if not RUNTIME_ROOT.is_dir() or any(
        p.is_symlink() for p in (RUNTIME_ROOT, *RUNTIME_ROOT.parents)
    ):
        raise ValueError("FIXED_PRIVATE_CLOSURE_ROOT_REQUIRED")
    metadata = RUNTIME_ROOT.stat()
    if metadata.st_uid != 1001 or metadata.st_mode & 0o077 or os.getuid() != 1001:
        raise ValueError("PRIVATE_UNPRIVILEGED_CLOSURE_ROOT_REQUIRED")


def prepare_code_receipt(repo: Path, revision: str) -> None:
    validate_private_root()

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(repo), *args])

    if git("rev-parse", "HEAD").decode().strip() != revision or git("status", "--porcelain"):
        raise ValueError("EXACT_CLEAN_CHECKOUT_REQUIRED")
    # Record the whole production/config tree, including imported helpers, once on the Git host.
    paths = git("ls-files", "-z", "src/quanthus", "config").decode().split("\0")
    files = {p: _sha256(repo / p) for p in paths if p}
    write_new(RUNTIME_ROOT / "code-receipt.json", {"code_revision": revision, "files": files})


def verify_code_receipt(repo: Path, revision: str) -> str:
    validate_private_root()
    receipt_path = RUNTIME_ROOT / "code-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    implementation = "src/quanthus/expert_strategy/p3_temporary_industry_closure.py"
    if receipt["code_revision"] != revision or implementation not in receipt["files"]:
        raise ValueError("IMPLEMENTATION_RECEIPT_MISMATCH")
    for relative, expected in receipt["files"].items():
        path = repo / relative
        if not path.resolve().is_relative_to(repo.resolve()) or _sha256(path) != expected:
            raise ValueError("IMPLEMENTATION_BYTES_MISMATCH")
    if _sha256(Path(__file__)) != receipt["files"][implementation]:
        raise ValueError("LOADED_IMPLEMENTATION_MISMATCH")
    return _sha256(receipt_path)


def checked_output_payloads(artifacts: dict[str, Any], maximum: int) -> dict[str, bytes]:
    payloads = {name: canonical_json_bytes(value) + b"\n" for name, value in artifacts.items()}
    if sum(map(len, payloads.values())) > maximum:
        raise ValueError("OUTPUT_BUDGET_EXCEEDED")
    return payloads


def compose(
    identities: list[dict[str, Any]],
    dwd: dict[str, str],
    p4: dict[str, tuple[str, str, str]],
    temporary: dict[str, Any],
    temporary_level1: str,
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, int]]:
    target = temporary["target"]
    members = []
    provenance = []
    seen = set()
    counts: Counter[str] = Counter()
    for identity in identities:
        security = identity["security_id"]
        if security in seen:
            raise ValueError("DUPLICATE_SECURITY_IDENTITY")
        seen.add(security)
        if security in dwd:
            industry, origin = dwd[security], "dwd_native"
            if security in p4 and p4[security][0] != industry:
                raise ValueError("DWD_P4_CONFLICT")
        elif security in p4:
            industry, origin = p4[security][0], "p4_sealed_reference"
        elif security == target["security_id"]:
            if identity["company_id"] != target["company_id"] or not any(
                binding["listing_id"] == target["listing_id"]
                and binding["listing_code"] == target["security_code"]
                and binding["venue_id"] == target["exchange"]
                and binding["first_session"]
                <= temporary["request"]["query_date"]
                <= binding["last_session"]
                for binding in identity["listing_bindings"]
            ):
                raise ValueError("TEMPORARY_IDENTITY_MISMATCH")
            industry, origin = temporary_level1, "temporary_static_source"
        else:
            raise ValueError("UNRESOLVED_SECURITY_IDENTITY")
        # Stock code is an audit label only. The full dated aliases remain in the separate ledger.
        first_binding = min(
            identity["listing_bindings"], key=lambda b: (b["first_session"], b["listing_id"])
        )
        members.append(
            {
                "security_id": security,
                "stock_code": first_binding["listing_code"],
                "exchange": first_binding["venue_id"],
                "level1_industry_id": industry,
            }
        )
        provenance.append({**identity, "source": origin, "level1_industry_id": industry})
        counts[origin] += 1
    if counts["temporary_static_source"] != 1:
        raise ValueError("EXACT_TEMPORARY_RESIDUAL_REQUIRED")
    return (
        sorted(members, key=lambda m: m["security_id"]),
        sorted(provenance, key=lambda m: m["security_id"]),
        dict(counts),
    )


def build(
    repo: Path,
    run_root: Path,
    base: Path,
    inspection: Path,
    temporary_root: Path,
    output: Path,
    revision: str,
) -> dict[str, Any]:
    import duckdb

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("EXACT_IMPLEMENTATION_REVISION_REQUIRED")
    code_receipt_sha = verify_code_receipt(repo, revision)
    if output != RUNTIME_ROOT / "attempt":
        raise ValueError("FIXED_PRIVATE_OUTPUT_REQUIRED")
    request_path = repo / "config/research/p3-strat-003/temporary-industry-closure.v1.json"
    request = checked_json(request_path, REQUEST_SHA)
    started = time.monotonic()
    stages = []
    proposal, hierarchy, activation_sha = validate_inputs(repo, inspection)
    prior = checked_json(inspection, request["inspection_manifest_sha256"])
    temporary = checked_json(temporary_root / "receipt.json", request["temporary_receipt_sha256"])
    response = checked_json(temporary_root / "response.json", request["temporary_response_sha256"])
    if (
        temporary["disposition"] != "QUALIFIED_TEMPORARY_STATIC_SOURCE"
        or temporary["activation_sha256"] != activation_sha
        or temporary["response_sha256"] != request["temporary_response_sha256"]
        or qualify(response, proposal, hierarchy) != temporary["qualified_mapping"]
    ):
        raise ValueError("TEMPORARY_SOURCE_QUALIFICATION_MISMATCH")
    inspection_request = json.loads((repo / request["inspection_request_path"]).read_bytes())
    if (
        digest_value(inspection_request) != REQUEST_DIGEST
        or request["inspection_request_digest"] != REQUEST_DIGEST
    ):
        raise ValueError("INSPECTION_REQUEST_DRIFT")
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("SYMLINK_OUTPUT_FORBIDDEN")
    for source in (repo, run_root, base, inspection.parent, temporary_root):
        if output.resolve().is_relative_to(source.resolve()):
            raise ValueError("OUTPUT_MUST_BE_ISOLATED_FROM_INPUTS")
    output.mkdir(parents=False, exist_ok=False, mode=0o700)
    failed = inspection_request["failed_job"]
    candidate_path = (
        run_root
        / ".staging"
        / failed["job_id"]
        / str(failed["claim_generation"])
        / failed["candidate_relative_path"]
    )
    if _sha256(candidate_path) != proposal["prerequisites"]["candidate_sha256"]:
        raise ValueError("RETAINED_CANDIDATE_DRIFT")
    _verify_dwd_industry_source(
        base, manifest_sha256=prior["inspection"]["dwd_base_manifest_sha256"]
    )
    p4_receipt, p4, _ = _p4_reference(
        run_root, inspection_request["p4_reference"], json_limit=524288
    )
    stages.append(
        {
            "stage": "source_integrity",
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "disposition": "verified_existing_bytes",
            "candidate_bytes": candidate_path.stat().st_size,
            "lifecycle": "none",
        }
    )
    stage_started = time.monotonic()
    inventory, identities = _candidate_inventory(
        run_root, failed, decision_start=date(2025, 1, 1), decision_end=date(2026, 9, 17)
    )
    if (
        inventory["sha256"] != proposal["prerequisites"]["candidate_sha256"]
        or inventory["identity_bindings_digest"]
        != proposal["prerequisites"]["identity_bindings_digest"]
        or inventory["security_count"] != request["expected_security_count"]
        or inventory["decision_security_count"] != request["expected_decision_security_count"]
    ):
        raise ValueError("RETAINED_IDENTITY_CLOSURE_DRIFT")
    stages.append(
        {
            "stage": "retained_identity_inventory",
            "elapsed_seconds": round(time.monotonic() - stage_started, 6),
            "rows": len(identities),
            "source_rows": inventory["row_count"],
            "bytes": len(canonical_json_bytes(identities)),
            "disposition": "built_once",
            "lifecycle": "TMP_in_memory",
        }
    )
    stage_started = time.monotonic()
    dwd, _ = _dwd_reference(
        base, identities, manifest_sha256=prior["inspection"]["dwd_base_manifest_sha256"]
    )
    node_expression = _read_parquet_expression(_table_paths(base, "dwd_industry_node"))
    connection = duckdb.connect(":memory:")
    try:
        nodes = connection.execute(
            f"SELECT DISTINCT industry_node_id FROM {node_expression} "
            "WHERE knowledge_to IS NULL AND node_level=1 AND node_code=?",
            [temporary["qualified_mapping"]["level1_code"]],
        ).fetchall()
    finally:
        connection.close()
    if len(nodes) != 1 or not nodes[0][0]:
        raise ValueError("TEMPORARY_LEVEL1_NODE_AMBIGUOUS_OR_MISSING")
    members, identities_with_source, counts = compose(
        identities, dwd, p4, temporary, str(nodes[0][0])
    )
    if counts != request["expected_sources"]:
        raise ValueError("SOURCE_DENOMINATOR_DRIFT")
    source_proof = {
        "inspection_sha256": request["inspection_manifest_sha256"],
        "temporary_receipt_sha256": request["temporary_receipt_sha256"],
        "candidate": inventory,
        "p4_reference": p4_receipt,
        "base_manifest_sha256": prior["inspection"]["dwd_base_manifest_sha256"],
        "identity_provenance_digest": digest_value(identities_with_source),
    }
    unsigned = {
        "schema_version": "1.0.0",
        "mapping_version": request["mapping_version"],
        "usage": request["usage"],
        "source": {
            "provider": "quanthus-p3-temporary-industry-closure",
            "endpoint": "sealed-dwd+p4+approved-one-security-source",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_revision": digest_value(source_proof),
        },
        "memberships": members,
    }
    mapping = {**unsigned, "artifact_digest": digest_value(unsigned)}
    artifacts = {
        "static_industry_mapping.json": mapping,
        "identity-provenance.json": {"sources": source_proof, "identities": identities_with_source},
    }
    payloads = checked_output_payloads(artifacts, request["limits"]["maximum_output_bytes"])
    for name, payload in payloads.items():
        (output / name).write_bytes(payload)
    stages.append(
        {
            "stage": "compose_static_mapping",
            "elapsed_seconds": round(time.monotonic() - stage_started, 6),
            "rows": len(members),
            "bytes": sum(map(len, payloads.values())),
            "disposition": "built_once_reused_identity_TMP",
            "lifecycle": "private_temporary_research",
        }
    )
    stage_started = time.monotonic()
    loaded = load_static_industry_mapping(
        output / "static_industry_mapping.json",
        expected_version=request["mapping_version"],
        required_security_ids=[i["security_id"] for i in identities],
    )
    if set(loaded.by_security_id) != {i["security_id"] for i in identities}:
        raise ValueError("EXACT_COVERAGE_REQUIRED")
    stages.append(
        {
            "stage": "verify_exact_coverage",
            "elapsed_seconds": round(time.monotonic() - stage_started, 6),
            "rows": len(loaded.by_security_id),
            "bytes": len(payloads["static_industry_mapping.json"]),
            "disposition": "reused_composed_mapping",
            "lifecycle": "none",
        }
    )
    result = {
        "schema_version": "quanthus.p3-temporary-industry-closure-receipt.v1",
        "disposition": "COMPLETE_TEMPORARY_STATIC_INDUSTRY_CLOSURE",
        "code_revision": revision,
        "code_receipt_sha256": code_receipt_sha,
        "implementation_sha256": _sha256(Path(__file__)),
        "request_sha256": _sha256(request_path),
        "source_proof": source_proof,
        "source_counts": counts,
        "security_count": len(members),
        "missing_count": 0,
        "duplicate_count": 0,
        "conflict_count": 0,
        "data_repair_issue": 470,
        "formal_DWD_repair": False,
        "formal_B_job": False,
        "B_accepted": False,
        "D_activated": False,
        "candidate_rebuilt": False,
        "candidate_scored": False,
        "strategy_or_outcome_results_read": False,
        "P6_mutated": False,
        "temporary_research_only": True,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stage_graph": request["stage_graph"],
        "stages": stages,
        "files": {
            name: {"sha256": _sha256(output / name), "size_bytes": (output / name).stat().st_size}
            for name in artifacts
        },
    }
    complete_payloads = checked_output_payloads(
        {**artifacts, "receipt.json": result}, request["limits"]["maximum_output_bytes"]
    )
    (output / "receipt.json").write_bytes(complete_payloads["receipt.json"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    for name in ("run-root", "base", "inspection", "temporary-root", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--prepare-code-receipt", action="store_true")
    args = parser.parse_args()
    if args.prepare_code_receipt:
        prepare_code_receipt(args.repo, args.revision)
        return
    if any(
        value is None
        for value in (args.run_root, args.base, args.inspection, args.temporary_root, args.output)
    ):
        parser.error("All source/output paths are required for closure composition")

    def timeout(_signum: int, _frame: Any) -> None:
        raise SystemExit("TEMPORARY_SOURCE_CLOSURE_DEADLINE_EXCEEDED")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(120)
    result = build(
        args.repo,
        args.run_root,
        args.base,
        args.inspection,
        args.temporary_root,
        args.output,
        args.revision,
    )
    signal.alarm(0)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "disposition",
                    "source_counts",
                    "security_count",
                    "files",
                    "elapsed_seconds",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
