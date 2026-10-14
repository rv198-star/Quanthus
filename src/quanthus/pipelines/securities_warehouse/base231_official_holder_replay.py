"""Offline independent reconstruction of bounded official holder matrices."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, load_contract
from .base22_bounded import BoundedError
from .base22_sources import _tree, physical_path
from .base231_official_holder import CONTRACT_ID, project_top_matrix
from .base231_official_profile import QUALIFIED_PARENT
from .financial_reporting import financial_company_resolver_from_listed_company_rows
from .ownership_capital import ownership_capital_schemas


def replay_official_holder(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    root = Path(root)
    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("historical_PIT_qualified") is not False
        or plan.get("old_values_overwritten") is not False
        or plan.get("source_priority_or_transaction_equivalence_granted") is not False
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("OFFICIAL_HOLDER_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("OFFICIAL_HOLDER_OFFLINE_PHYSICAL_SET_CHANGED")
    for name, member in plan["members"].items():
        p = physical_path(root / name)
        if (
            root.resolve() not in p.parents
            or p.stat().st_nlink != 1
            or p.stat().st_size != member["bytes"]
            or file_sha(p) != member["sha256"]
        ):
            raise BoundedError("OFFICIAL_HOLDER_OFFLINE_MEMBER_CHANGED")
    for name, sha in plan["code_inputs"].items():
        if file_sha(Path(name)) != sha:
            raise BoundedError("OFFICIAL_HOLDER_OFFLINE_ORDINARY_CODE_CHANGED")
    parent = root / "inputs/qualified-parent-manifest.json"
    if file_sha(parent) != QUALIFIED_PARENT:
        raise BoundedError("OFFICIAL_HOLDER_RETAINED_NATIVE_PARENT_CHANGED")
    originals = {r["path"]: r for r in json.loads(parent.read_bytes())["members"]}
    rows: dict[str, list[dict[str, Any]]] = {}
    for table, specs in plan["parents"].items():
        rows[table] = []
        for spec in specs:
            path = root / spec["local"]
            if file_sha(path) != originals["domain/" + spec["original"]]["sha256"]:
                raise BoundedError("OFFICIAL_HOLDER_EXACT_NATIVE_PARENT_CHANGED")
            rows[table].extend(pq.ParquetFile(path).read().to_pylist())
    if set(rows) != {"dwd_security", "dwd_security_identifier"}:
        raise BoundedError("OFFICIAL_HOLDER_NATIVE_PARENT_ROLES_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        rows["dwd_security"], rows["dwd_security_identifier"]
    )
    image_manifest_path = root / "inputs/identity-image-manifest.json"
    if (
        file_sha(image_manifest_path)
        != "sha256:91845422851368028e4e8a6b83e0dc9ba2a9e30dfdd8e4d6de892f64adac1d7f"
    ):
        raise BoundedError("OFFICIAL_HOLDER_EXACT_COMPANY_IDENTIFIER_PARENT_CHANGED")
    image_manifest = json.loads(image_manifest_path.read_bytes())
    materialization_path = root / "inputs/identity-image-materialization.json"
    if file_sha(materialization_path) != image_manifest["materialization_sha256"]:
        raise BoundedError("OFFICIAL_HOLDER_NATIVE_COMPANY_IDENTIFIER_LAYOUT_CHANGED")
    originals_for_org = {
        r["path"]: r
        for r in json.loads(materialization_path.read_bytes())["layout"]["dwd_company_identifier"][
            "files"
        ]
    }
    org_bindings = []
    if {r["original"] for r in plan["company_identifier_members"]} != set(originals_for_org):
        raise BoundedError("OFFICIAL_HOLDER_COMPANY_IDENTIFIER_PHYSICAL_SET_CHANGED")
    for member in plan["company_identifier_members"]:
        path = root / member["local"]
        if file_sha(path) != originals_for_org[member["original"]]["sha256"]:
            raise BoundedError("OFFICIAL_HOLDER_COMPANY_IDENTIFIER_NATIVE_CHANGED")
        org_bindings.extend(pq.ParquetFile(path).read().to_pylist())
    registration = json.loads((root / "inputs/api-registration.json").read_bytes())
    execution = json.loads((root / "inputs/api-execution.json").read_bytes())
    if execution["registration_sha256"] != file_sha(root / "inputs/api-registration.json"):
        raise BoundedError("OFFICIAL_HOLDER_HINT_COLLECTION_CHANGED")
    receipts = {r["label"]: r for r in execution["receipts"]}
    targets = {r["label"]: r for r in registration["targets"]}
    now = datetime.fromisoformat(plan["observed_at"])
    output = []
    proofs = []
    frames = set()
    for spec in plan["selection"]:
        label = spec["label"]
        if label in frames or spec["holding_type"] not in {
            "TOP_SHAREHOLDERS",
            "TOP_FLOATING_SHAREHOLDERS",
        }:
            raise BoundedError("OFFICIAL_HOLDER_DUPLICATE_OR_WRONG_SOURCE_FRAME")
        frames.add(label)
        receipt = json.loads((root / spec["receipt"]).read_bytes())
        if receipt != receipts[label] or any(
            receipt[k] != targets[label][k]
            for k in ["code", "business_date", "holding_type", "URI", "columns", "reportName"]
        ):
            raise BoundedError("OFFICIAL_HOLDER_EXACT_HINT_REQUEST_CHANGED")
        if (
            receipt["curl_exit"] != 0
            or receipt["http_status"] != "200"
            or file_sha(root / spec["body"]) != receipt["body_sha256"]
        ):
            raise BoundedError("OFFICIAL_HOLDER_REAL_HINT_BODY_CHANGED")
        data = json.loads((root / spec["body"]).read_bytes(), parse_float=str)
        result = data.get("result") or {}
        hints = result.get("data") or []
        if (
            data.get("success") is not True
            or result.get("count") != 10
            or result.get("pages") != 1
            or len(hints) != 10
            or any(
                h["SECURITY_CODE"] != spec["code"] or h["END_DATE"][:10] != spec["business_date"]
                for h in hints
            )
        ):
            raise BoundedError("OFFICIAL_HOLDER_EXACT_HINT_FRAME_CHANGED")
        parent_identity, _ = resolver.resolve_for_report(
            spec["code"], period_end=date.fromisoformat(spec["business_date"])
        )
        if parent_identity is None:
            raise BoundedError("OFFICIAL_HOLDER_DATED_COMPANY_IDENTITY_MISSING")
        qualified = []
        for candidate in plan["candidate_sources"][label]:
            original = json.loads((root / candidate["selection"]).read_bytes())
            raw_index = json.loads((root / candidate["index"]).read_bytes())
            original_row = original["actual_index_row"]
            document_id = str(original_row["announcementId"])
            original_receipt = json.loads((root / candidate["receipt"]).read_bytes())
            if (
                file_sha(root / candidate["index"]) != original["index_sha256"]
                or original_row not in (raw_index.get("announcements") or [])
                or original_row["secCode"] != spec["code"]
                or original_receipt["URI"]
                != "https://static.cninfo.com.cn/" + original_row["adjunctUrl"]
                or original_receipt["http_status"] != "200"
                or original_receipt["curl_exit"] != 0
                or file_sha(root / candidate["PDF"]) != original_receipt["body_sha256"]
            ):
                raise BoundedError("OFFICIAL_HOLDER_COMPLETE_ORIGINAL_DOCUMENT_INDEX_CHANGED")
            text = subprocess.run(
                ["pdftotext", "-layout", str(root / candidate["PDF"]), "-"],
                check=True,
                capture_output=True,
                timeout=60,
            ).stdout.decode()
            captured = max(
                datetime.fromisoformat(receipt["completed_at"]),
                datetime.fromisoformat(original_receipt["completed_at"]),
            )
            try:
                native, proof = project_top_matrix(
                    text,
                    hints,
                    parent=parent_identity,
                    business_date=date.fromisoformat(spec["business_date"]),
                    announcement_id=document_id,
                    official_sha256=file_sha(root / candidate["PDF"]),
                    hint_sha256=file_sha(root / spec["body"]),
                    source_known_at=captured,
                    observed_at=now,
                    original_index=original_row,
                    company_org_bindings=org_bindings,
                    holding_type=spec["holding_type"],
                )
            except (RuntimeError, ValueError):
                continue
            meta = {
                "record_source",
                "source_record_id",
                "source_revision",
                "source_artifact_sha256",
                "knowledge_from",
                "knowledge_to",
                "parser_version",
            }
            business = digest([{k: v for k, v in r.items() if k not in meta} for r in native])
            qualified.append(
                (file_sha(root / candidate["PDF"]), document_id, native, proof, business)
            )
        if (
            not qualified
            or len({r[-1] for r in qualified}) != 1
            or len(qualified) != spec["equal_official_business_variants"]
        ):
            raise BoundedError("OFFICIAL_HOLDER_SOURCE_MATRIX_CONSENSUS_CHANGED")
        _, chosen_id, native, proof, business = min(qualified, key=lambda r: r[0])
        if (
            chosen_id != spec["announcement_id"]
            or business != spec["business_sha256"]
            or digest(proof) != spec["proof_sha256"]
        ):
            raise BoundedError("OFFICIAL_HOLDER_EXACT_SOURCE_SELECTION_CHANGED")
        output.extend(native)
        proofs.append(proof)
    actual = pq.ParquetFile(root / "outputs/dwd_shareholding_observation.parquet").read()
    expected_schema = ownership_capital_schemas()["dwd_shareholding_observation"]
    if (
        actual.schema != expected_schema
        or actual.to_pylist() != output
        or len(output) != plan["rows"]
        or len(frames) != plan["frames"]
        or digest(output) != plan["semantic_sha256"]
        or digest(proofs) != plan["proof_sha256"]
    ):
        raise BoundedError("OFFICIAL_HOLDER_FULL_NATIVE_RECONSTRUCTION_CHANGED")
    contract, _, _ = load_contract(root / "config")
    keys = contract["tables"]["dwd_shareholding_observation"]["version_key"]
    if len({digest([r[k] for k in keys]) for r in output}) != len(output):
        raise BoundedError("OFFICIAL_HOLDER_GLOBAL_VERSION_DUPLICATE")
    return dict(
        status="PASS_FULL_LOCAL_OFFICIAL_HOLDER_NATIVE_REPLAY",
        rows=len(output),
        frames=len(frames),
        input_identity=plan["identity"],
        complete_Base=False,
        source_network_or_old_roots_required=False,
        historical_PIT_qualified=False,
        consumer_admission="NOT_GRANTED",
        old_values_overwritten=False,
        source_priority_or_transaction_equivalence_granted=False,
    )
