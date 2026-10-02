"""One approved industry lookup, isolated from formal DWD and strategy execution."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import signal
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROPOSAL_SHA = "sha256:72127ad06b82a3d5ca087f30b308200183a1bbf2b475fc95df0f6ea38a9dd744"
FIELDS = ["updateDate", "code", "code_name", "industry", "industryClassification"]
# Same host path is mounted at the same container path. A new --output cannot reopen authority.
RUNTIME_ROOT = Path("/home/ubuntu/quanthus-research/p3-p5-20261002/p3-temporary-source")
CODE_FILES = (
    "src/quanthus/p3_temporary_industry.py",
    "config/research/p3-p5-temporary-source-activation.v1.json",
    "config/research/p3-strat-003/single-security-industry-proposal.v1.json",
    "config/pipelines/csrc-industry-hierarchy.v1.json",
)


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def checked_json(path: Path, expected: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if sha(raw) != expected:
        raise ValueError("SOURCE_CHECKSUM_MISMATCH")
    return json.loads(raw)


def validate_inputs(root: Path, inspection: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    activation_path = root / "config/research/p3-p5-temporary-source-activation.v1.json"
    activation_bytes = activation_path.read_bytes()
    activation = json.loads(activation_bytes)
    bound = activation["sources"]["P3"]
    if (
        activation["state"] != "ACTIVE_BOUNDED_TEMPORARY_QUALIFICATION"
        or activation["usage"] != "temporary_research_qualification_only"
        or activation["temporary_success_closes_data_issues"] is not False
        or activation["P6_or_production_mutation"] is not False
        or bound["data_repair_issues"] != [470]
        or bound["proposal_sha256"] != PROPOSAL_SHA
    ):
        raise ValueError("TEMPORARY_ACTIVATION_INVALID")
    proposal = checked_json(root / bound["proposal_path"], PROPOSAL_SHA)
    prerequisite = proposal["prerequisites"]
    manifest = checked_json(inspection, prerequisite["inspection_manifest_sha256"])
    projection = manifest["inspection"]["candidate_projection"]
    coverage = manifest["inspection"]["coverage"]
    if (
        manifest["result_digest"] != prerequisite["inspection_result_digest"]
        or projection["sha256"] != prerequisite["candidate_sha256"]
        or projection["identity_bindings_digest"] != prerequisite["identity_bindings_digest"]
        or coverage["remaining_unresolved_security_count"] != 1
        or len(coverage["remaining_unresolved_samples"]) != 1
    ):
        raise ValueError("INSPECTION_IDENTITY_MISMATCH")
    target = proposal["target"]
    row = coverage["remaining_unresolved_samples"][0]
    for key in ("security_id", "company_id"):
        if row[key] != target[key]:
            raise ValueError("UNQUALIFIED_IDENTITY_AT_QUERY_DATE")
    bindings = [
        b
        for b in row["listing_bindings"]
        if (b["first_session"] <= proposal["source"]["query_date"] <= b["last_session"])
    ]
    if len(bindings) != 1 or any(
        bindings[0][key] != value
        for key, value in {
            "listing_id": target["listing_id"],
            "listing_code": target["security_code"],
            "venue_id": target["exchange"],
        }.items()
    ):
        raise ValueError("UNQUALIFIED_IDENTITY_AT_QUERY_DATE")
    source = proposal["source"]
    hierarchy = checked_json(root / source["hierarchy_path"], source["hierarchy_sha256"])
    return proposal, hierarchy, sha(activation_bytes)


def qualify(
    response: dict[str, Any], proposal: dict[str, Any], hierarchy: dict[str, Any]
) -> dict[str, str]:
    source = proposal["source"]
    if response["error_code"] != "0" or response["fields"] != FIELDS:
        raise ValueError("UNQUALIFIED_PROVIDER_RESPONSE")
    rows = response["rows"]
    if not rows:
        raise ValueError("UNAVAILABLE_COMPLETE_EMPTY_RESPONSE")
    if len(rows) != 1 or len(rows[0]) != len(FIELDS):
        raise ValueError("UNQUALIFIED_RESPONSE_CARDINALITY")
    update, code, _name, industry, classification = rows[0]
    if code != source["provider_code"] or classification != source["classification"]:
        raise ValueError("UNQUALIFIED_PROVIDER_IDENTITY_OR_CLASSIFICATION")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", update):
        raise ValueError("UNQUALIFIED_PROVIDER_DATE")
    if date.fromisoformat(update) > date.fromisoformat(source["query_date"]):
        raise ValueError("UNQUALIFIED_FUTURE_PROVIDER_DATE")
    match = re.fullmatch(r"([A-Z]\d+)(.+)", industry)
    if not match:
        raise ValueError("UNQUALIFIED_INDUSTRY_HIERARCHY")
    leaf, name = match.groups()
    if hierarchy["leaf_nodes"].get(leaf) != name or leaf[0] not in hierarchy["parent_nodes"]:
        raise ValueError("UNQUALIFIED_INDUSTRY_HIERARCHY")
    return {
        "leaf_code": leaf,
        "leaf_name": name,
        "level1_code": leaf[0],
        "level1_name": hierarchy["parent_nodes"][leaf[0]],
        "provider_update_date": update,
    }


def write_new(path: Path, value: Any) -> None:
    # Exclusive durable write. Existing/uncertain attempts must never silently query again.
    with path.open("xb") as stream:
        stream.write(encoded(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def prepare_code_receipt(root: Path, revision: str) -> None:
    """Host Git is available; the isolated image intentionally contains no Git executable."""
    validate_private_root()

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args])

    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or git("rev-parse", "HEAD").decode().strip() != revision
    ):
        raise ValueError("CHECKOUT_REVISION_MISMATCH")
    files = {}
    for relative in CODE_FILES:
        expected = sha(git("show", f"{revision}:{relative}"))
        if sha((root / relative).read_bytes()) != expected:
            raise ValueError("CHECKOUT_SOURCE_CHANGED")
        files[relative] = expected
    write_new(
        RUNTIME_ROOT / "code-receipt.json",
        {
            "code_revision": revision,
            "git_blob_verification": True,
            "files": files,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def verify_code_receipt(root: Path, revision: str) -> str:
    raw = (RUNTIME_ROOT / "code-receipt.json").read_bytes()
    receipt = json.loads(raw)
    if (
        receipt["code_revision"] != revision
        or receipt["git_blob_verification"] is not True
        or set(receipt["files"]) != set(CODE_FILES)
    ):
        raise ValueError("IMPLEMENTATION_RECEIPT_MISMATCH")
    for relative, expected in receipt["files"].items():
        if sha((root / relative).read_bytes()) != expected:
            raise ValueError("IMPLEMENTATION_BYTES_MISMATCH")
    if sha(Path(__file__).read_bytes()) != receipt["files"][CODE_FILES[0]]:
        raise ValueError("LOADED_IMPLEMENTATION_MISMATCH")
    return sha(raw)


def validate_private_root() -> None:
    if not RUNTIME_ROOT.is_dir():
        raise ValueError("FIXED_PRIVATE_ATTEMPT_ROOT_REQUIRED")
    if any(part.is_symlink() for part in [RUNTIME_ROOT, *RUNTIME_ROOT.parents]):
        raise ValueError("SYMLINK_ATTEMPT_ROOT_FORBIDDEN")
    metadata = RUNTIME_ROOT.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("PRIVATE_OWNED_ATTEMPT_ROOT_REQUIRED")


def run(root: Path, inspection: Path, output: Path, revision: str) -> dict[str, Any]:
    validate_private_root()
    proposal, hierarchy, activation_sha = validate_inputs(root, inspection)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("IMPLEMENTATION_REVISION_REQUIRED")
    code_receipt_sha = verify_code_receipt(root, revision)
    if importlib.metadata.version("baostock") != proposal["source"]["provider_version"]:
        raise ValueError("PROVIDER_VERSION_MISMATCH")
    import baostock as bs

    if output != RUNTIME_ROOT / "attempt":
        raise ValueError("FIXED_PRIVATE_ATTEMPT_ROOT_REQUIRED")
    if output.is_symlink():
        raise ValueError("SYMLINK_ATTEMPT_ROOT_FORBIDDEN")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    directory = os.open(RUNTIME_ROOT, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    receipt: dict[str, Any] = {
        "schema_version": "quanthus.p3-temporary-industry-receipt.v1",
        "code_revision": revision,
        "implementation_sha256": sha(Path(__file__).read_bytes()),
        "code_receipt_sha256": code_receipt_sha,
        "proposal_sha256": PROPOSAL_SHA,
        "activation_sha256": activation_sha,
        "data_repair_issue": 470,
        "temporary_research_only": True,
        "formal_DWD_repair": False,
        "B_accepted": False,
        "D_activated": False,
        "usage": "static_non_pit_concentration_only",
        "historical_as_known": False,
        "request": proposal["source"],
        "target": proposal["target"],
        "retrieved_at": None,
        "response_sha256": None,
        "response_semantic_digest": None,
        "capture_format": "canonical JSON of provider fields/rows/errors; not raw wire bytes",
        "query_calls": 0,
        "transport_retries": 0,
        "qualified_mapping": None,
        "disposition": "UNKNOWN_INTERRUPTED_ATTEMPT_DO_NOT_REQUERY",
    }
    write_new(output / "attempt.json", receipt)
    started = time.monotonic()

    def timeout(_signum: int, _frame: Any) -> None:
        raise TimeoutError("PROVIDER_DEADLINE_EXCEEDED")

    old_handler = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(proposal["budget"]["maximum_wall_seconds"])
    response: dict[str, Any] = {"fields": [], "rows": [], "error_code": None}
    try:
        login = bs.login()
        if login.error_code != "0":
            raise ValueError("PROVIDER_LOGIN_UNAVAILABLE")
        # No automatic retry is chosen: it is within the approved maximum of one.
        receipt["query_calls"] = 1
        result = bs.query_stock_industry(
            code=proposal["source"]["provider_code"], date=proposal["source"]["query_date"]
        )
        response["fields"] = list(result.fields)
        response["error_code"] = str(result.error_code)
        while result.error_code == "0" and result.next():
            response["rows"].append(list(result.get_row_data()))
            if len(encoded(response)) > proposal["budget"]["maximum_response_bytes"]:
                raise ValueError("PROVIDER_RESPONSE_BUDGET_EXCEEDED")
        response["error_code"] = str(result.error_code)
        capture = encoded(response) + b"\n"
        if len(capture) > proposal["budget"]["maximum_response_bytes"]:
            raise ValueError("PROVIDER_RESPONSE_BUDGET_EXCEEDED")
        receipt["retrieved_at"] = datetime.now(timezone.utc).isoformat()
        write_new(output / "response.json", response)
        receipt["response_sha256"] = sha(capture)
        receipt["response_semantic_digest"] = sha(encoded(response))
        receipt["qualified_mapping"] = qualify(response, proposal, hierarchy)
        receipt["disposition"] = "QUALIFIED_TEMPORARY_STATIC_SOURCE"
    except (ValueError, TimeoutError) as exc:
        receipt["disposition"] = str(exc)
    except Exception as exc:
        receipt["disposition"] = "PROVIDER_INCOMPLETE_OR_UNKNOWN_DO_NOT_REQUERY"
        receipt["error_type"] = type(exc).__name__
    finally:
        # Logout can block on a dead connection; the process owns this single connection.
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_new(output / "receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inspection", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--prepare-code-receipt", action="store_true")
    args = parser.parse_args()
    if args.prepare_code_receipt:
        prepare_code_receipt(args.root, args.revision)
        return
    if args.inspection is None or args.output is None:
        parser.error("--inspection and --output are required for collection")
    receipt = run(args.root, args.inspection, args.output, args.revision)
    print(
        json.dumps(
            {
                key: receipt[key]
                for key in (
                    "disposition",
                    "query_calls",
                    "transport_retries",
                    "response_sha256",
                    "elapsed_seconds",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
