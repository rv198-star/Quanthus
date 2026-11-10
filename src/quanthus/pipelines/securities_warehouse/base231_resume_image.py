"""Exact suspension-end successor, with source-local replay and native full scan.

Only three pinned knowledge versions change. Prior raw repair closures and all
other entities remain byte-identical; archived source roots are never reopened.
"""

from __future__ import annotations

import json
import re
import subprocess
import zlib
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_sources import _member, _tree
from .base231_resume_supplement import revise_suspension_end

TABLE = "dwd_listing_status_interval"
PREFIX = "evidence/base231-resume"
KIND = "p10_base231_exact_official_resume_successor_v1"
PARENT = "sha256:669779cec9114f5ed516fc746465262c535f46dcb3d832a457940ebf853e0042"
PACKET = "sha256:ba48fcdc8fa0f9596c2a7ed5c8108a07b3c177f8b606f1b334c9d5e075b79da9"
CLOCK_FIELDS = {
    "knowledge_from",
    "knowledge_to",
    "effective_from",
    "effective_to",
    "observation_start",
    "observation_end",
    "open_at",
    "close_at",
}


def typed(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for name, value in result.items():
        if value is not None and name in CLOCK_FIELDS:
            result[name] = datetime.fromisoformat(value)
        elif value is not None and name == "trade_date":
            result[name] = date.fromisoformat(value)
    return result


def merge_versions(
    before: list[dict[str, Any]], revisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Exact paired replacements: close original knowledge, append its successor."""

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        return (r["listing_status_id"], r["effective_from"], r["knowledge_from"])

    old = {key(r): r for r in before}
    closed = {key(r): r for r in revisions if r["knowledge_to"] is not None}
    new = [r for r in revisions if r["knowledge_to"] is None]
    if len(old) != len(before) or len(closed) != 3 or len(new) != 3 or len(revisions) != 6:
        raise BoundedError("RESUME_IMAGE_EXACT_VERSION_CENSUS_INVALID")
    allowed = {
        "knowledge_from",
        "effective_to",
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
    }
    for identity, row in closed.items():
        original = old.get(identity)
        if (
            original is None
            or original["knowledge_to"] is not None
            or {**row, "knowledge_to": None} != original
            or row["knowledge_to"] <= row["knowledge_from"]
        ):
            raise BoundedError("RESUME_IMAGE_UNAUTHORIZED_OLD_VERSION_CHANGE")
        pairs = [
            r
            for r in new
            if r["listing_status_id"] == row["listing_status_id"]
            and r["effective_from"] == row["effective_from"]
            and r["knowledge_from"] == row["knowledge_to"]
        ]
        if len(pairs) != 1:
            raise BoundedError("RESUME_IMAGE_SUCCESSOR_PAIR_INVALID")
        after = pairs[0]
        if (
            set(after) != set(original)
            or any(after[k] != original[k] for k in original if k not in allowed)
            or original["effective_to"] is not None
            or after["effective_to"] is None
            or not original["effective_from"] < after["effective_to"]
            or after["record_source"] != "cninfo.official_issuer_resume"
        ):
            raise BoundedError("RESUME_IMAGE_UNAUTHORIZED_BUSINESS_ATOM_CHANGE")
    result = [closed.get(key(r), r) for r in before] + new
    if len({key(r) for r in result}) != len(result):
        raise BoundedError("RESUME_IMAGE_DUPLICATE_OUTPUT_VERSION")
    return sorted(result, key=key)


def replay_packet(root: Path) -> list[dict[str, Any]]:
    """Raw protocol/PDF replay; only this packet supplies acquisition evidence."""
    import baostock.common.contants as constants  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    def read(name: str) -> dict[str, Any]:
        return dict(json.loads((root / name).read_bytes()))

    if file_sha(root / "manifest.json") != PACKET:
        raise BoundedError("RESUME_IMAGE_PINNED_PACKET_CHANGED")
    manifest = read("manifest.json")
    if _tree(root) != {m["path"] for m in manifest["files"]} | {"manifest.json", "READY"}:
        raise BoundedError("RESUME_IMAGE_PACKET_PHYSICAL_SET_CHANGED")
    for item in manifest["files"]:
        p = _member(root, item["path"])
        if (
            p.stat().st_nlink != 1
            or p.stat().st_size != item["bytes"]
            or file_sha(p) != item["sha256"]
        ):
            raise BoundedError("RESUME_IMAGE_PACKET_BYTES_CHANGED")
    if read("READY")["manifest_sha256"] != PACKET:
        raise BoundedError("RESUME_IMAGE_PACKET_READY_CHANGED")
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for path in sorted((root / "raw").glob("wire-response-*.bin")):
        raw = path.read_bytes()
        header = raw[: constants.MESSAGE_HEADER_LENGTH].decode().split(constants.MESSAGE_SPLIT)
        payload = raw[constants.MESSAGE_HEADER_LENGTH :]
        if header[1] in constants.COMPRESSED_MESSAGE_TYPE_TUPLE:
            payload = zlib.decompress(payload[: int(header[2])])
        body = payload.decode().split(constants.MESSAGE_SPLIT)
        if body[0] != "0":
            raise BoundedError("RESUME_IMAGE_PROVIDER_WIRE_ERROR")
        method = body[2]
        if method in {"login", "logout"}:
            continue
        if method not in {"query_all_stock", "query_stock_basic", "query_history_k_data_plus"}:
            raise BoundedError("RESUME_IMAGE_UNAPPROVED_PROVIDER_OPERATION")
        fields = [
            v.strip()
            for v in body[9 if method == "query_stock_basic" else 8].split(
                constants.ATTRIBUTE_SPLIT
            )
        ]
        rows = json.loads("".join(body[6].split()))["record"] if body[6].strip() else []
        groups[method, body[7]].append((int(body[4]), fields, rows))
    files = [*root.glob("boundary-*.json"), *root.glob("state-*.json"), *root.glob("basic-*.json")]
    if len(files) != 62:
        raise BoundedError("RESUME_IMAGE_PROVIDER_RESPONSE_CENSUS_CHANGED")
    for path in files:
        response = read(path.name)
        request = response["request"]
        method = (
            "query_all_stock"
            if path.name.startswith("boundary")
            else (
                "query_stock_basic"
                if path.name.startswith("basic")
                else "query_history_k_data_plus"
            )
        )
        pages = sorted(groups.pop((method, request.get("day", request.get("code"))), []))
        if (
            not pages
            or [p[0] for p in pages] != list(range(1, len(pages) + 1))
            or any(p[1] != response["fields"] for p in pages)
            or [row for p in pages for row in p[2]] != response["rows"]
        ):
            raise BoundedError("RESUME_IMAGE_PROVIDER_RAW_REPLAY_CHANGED")
    if groups:
        raise BoundedError("RESUME_IMAGE_UNREPLAYED_PROVIDER_RESPONSE")
    baseline, sessions = read("scoped-baseline-TMP.json"), read("resume-sessions.json")
    observed = datetime.fromisoformat(read("qualification.safe.json")["observed_at"])
    result = []
    for code in ["002036", "300242", "300955"]:
        binding = read("resume-binding-" + code + ".json")
        notice = binding["notice"]
        notice["resume_at"] = datetime.fromisoformat(notice["resume_at"])
        pdf = root / "raw" / (notice["announcement_id"] + ".pdf")
        text = subprocess.check_output(["pdftotext", "-layout", str(pdf), "-"], timeout=20).decode()
        if text != (pdf.with_suffix(".txt")).read_text():
            raise BoundedError("RESUME_IMAGE_OFFICIAL_BODY_REPLAY_CHANGED")
        compact = re.sub(r"\s+", "", text)
        day = notice["resume_at"].astimezone(ZoneInfo("Asia/Shanghai")).date()
        token = f"{day.year}年{day.month}月{day.day}日"
        if code not in compact or not re.search(
            re.escape(token) + r".{0,25}(开市起复牌|开市起恢复)", compact
        ):
            raise BoundedError("RESUME_IMAGE_OFFICIAL_RESUME_CLAIM_CHANGED")
        listing = typed(baseline["listings"][code])
        before = next(
            typed(r)
            for r in baseline["original_suspensions"]
            if r["listing_id"] == listing["listing_id"]
        )
        rows, proof = revise_suspension_end(
            before,
            listing,
            typed(sessions[code]),
            notice,
            read("state-sz." + code + ".json"),
            expected_days=[date.fromisoformat(d) for d in binding["expected_days"]],
            binding=binding["binding"],
            observed_at=observed,
        )
        if digest(proof) != digest(
            {
                **binding["proof"],
                "resume_at": notice["resume_at"],
                "knowledge_observed_at": observed,
            }
        ):
            raise BoundedError("RESUME_IMAGE_REPAIR_PROOF_CHANGED")
        result.extend(rows)
    if pq.ParquetFile(root / "suspension-end-new-knowledge.parquet").read().to_pylist() != result:
        raise BoundedError("RESUME_IMAGE_REPLAYED_VERSION_BYTES_CHANGED")
    return result


def validate_binding(root: Path) -> dict[str, Any]:
    scope_root = root / PREFIX
    body: dict[str, Any] = json.loads((scope_root / "SCOPE.json").read_bytes())
    if (
        body.get("kind") != KIND
        or body.get("predecessor_manifest_sha256") != PARENT
        or body.get("packet_manifest_sha256") != PACKET
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("RESUME_IMAGE_SCOPE_CHANGED")
    if _tree(scope_root) != set(body["physical_members"]) | {"SCOPE.json"}:
        raise BoundedError("RESUME_IMAGE_SCOPE_PHYSICAL_SET_CHANGED")
    for name, item in body["physical_members"].items():
        p = _member(scope_root, name)
        if (
            p.stat().st_nlink != 1
            or p.stat().st_size != item["size_bytes"]
            or file_sha(p) != item["sha256"]
        ):
            raise BoundedError("RESUME_IMAGE_SCOPE_BYTES_CHANGED")
    parent = json.loads((scope_root / "inputs/predecessor/MATERIALIZATION.json").read_bytes())
    manifest = json.loads((scope_root / "inputs/predecessor/manifest.json").read_bytes())
    if (
        file_sha(scope_root / "inputs/predecessor/manifest.json") != PARENT
        or file_sha(scope_root / "inputs/predecessor/MATERIALIZATION.json")
        != manifest["materialization_sha256"]
    ):
        raise BoundedError("RESUME_IMAGE_PARENT_DESCRIPTOR_CHANGED")
    original_files = parent["layout"][TABLE]["files"]
    if (
        len(original_files) != 1
        or file_sha(scope_root / "inputs/predecessor/status-before.parquet")
        != original_files[0]["sha256"]
    ):
        raise BoundedError("RESUME_IMAGE_ORIGINAL_STATUS_MEMBER_CHANGED")
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    if plan.get("resume_acceptance") != {
        "kind": KIND,
        "local_prefix": PREFIX,
        "scope_identity": body["identity"],
        "predecessor_manifest_sha256": PARENT,
    } or set(plan["tables"]) != set(parent["layout"]):
        raise BoundedError("RESUME_IMAGE_PLAN_BINDING_CHANGED")
    for table, entry in parent["layout"].items():
        expected = (
            body["outputs"]
            if table == TABLE
            else [
                {
                    **{k: m[k] for k in ["path", "partition", "rows", "size_bytes", "sha256"]},
                    "table": table,
                    "source_id": "predecessor",
                }
                for m in entry["files"]
            ]
        )
        if plan["tables"][table]["files"] != expected:
            raise BoundedError("RESUME_IMAGE_UNAUTHORIZED_ENTITY_CHANGE")
    # Check the changed physical entity before replay; disposable deletion or
    # corruption must reject without any fallback to archived roots.
    changed = root / f"payload/{TABLE}/all/part-000000.parquet"
    if not changed.is_file():
        raise BoundedError("RESUME_IMAGE_REQUIRED_FILE_MISSING")
    if changed.is_symlink() or changed.stat().st_nlink != 1:
        raise BoundedError("RESUME_IMAGE_ENTITY_NOT_INDEPENDENT")
    if (
        changed.stat().st_size != body["outputs"][0]["size_bytes"]
        or file_sha(changed) != body["outputs"][0]["sha256"]
    ):
        raise BoundedError("RESUME_IMAGE_ENTITY_BYTES_CHANGED")
    for name, sha in parent["evidence"].items():
        if name in {"evidence/input-plan.json", "evidence/limitations.json"} or name.startswith(
            "evidence/sources/"
        ):
            continue
        if file_sha(_member(root, name)) != sha:
            raise BoundedError("RESUME_IMAGE_INHERITED_EVIDENCE_CHANGED")
    return body


def audit_image(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq

    from .base23_native_image import audit_native_image
    from .base23_required_union import audit_required_union
    from .base231_image import PREFIX as PATCH
    from .base231_image import replay_scope
    from .base231_sparse_image import PREFIX as SPARSE
    from .base231_sparse_image import project_delta, validate_local_scope
    from .base231_sparse_pack import audit as sparse_audit

    body = validate_binding(root)
    local = root / PREFIX
    budget.begin_stage("official-resume-raw-and-allowed-merge-replay")
    revisions = replay_packet(local / "inputs/packet")
    prior = pq.ParquetFile(local / "inputs/predecessor/status-before.parquet").read().to_pylist()
    merged = merge_versions(prior, revisions)
    output = pq.ParquetFile(local / body["outputs"][0]["path"]).read().to_pylist()
    if output != merged or len(output) != 8584:
        raise BoundedError("RESUME_IMAGE_EXACT_MERGE_REPLAY_CHANGED")
    validate_local_scope(root / SPARSE)
    budget.begin_stage("inherited-required-domain-replay")
    required = audit_required_union(root / "evidence/required-action-union", budget)
    patch = replay_scope(root / PATCH, budget)
    budget.begin_stage("inherited-sparse-source-replay")
    with Budget(budget.root / "sparse-replay", [root], budget.limits) as child:
        sparse = sparse_audit(root / SPARSE / "inputs/sparse", child)
    budget.begin_stage("inherited-sparse-exact-selection-replay")
    rules = json.loads((root / "contract/base22-independent-image.v1.json").read_bytes())
    with Budget(budget.root / "delta-replay", [root], budget.limits) as child:
        delta = project_delta(
            before=root,
            sparse=root / SPARSE / "inputs/sparse",
            rules=rules,
            budget=child,
            read_only_roots=[root],
            prior_descriptor=root / SPARSE / "inputs/predecessor/MATERIALIZATION.json",
        )
    old_sparse = json.loads((root / SPARSE / "SCOPE.json").read_bytes())
    if delta != old_sparse["delta"]:
        raise BoundedError("RESUME_IMAGE_INHERITED_SPARSE_DELTA_CHANGED")
    native = audit_native_image(root, budget)
    result = {
        "kind": KIND,
        "scope_identity": body["identity"],
        "required_identity": required["identity"],
        "patch_receipt": patch,
        "sparse_receipt": sparse,
        "native_receipt": native,
        "changed_old_versions": 3,
        "new_versions": 3,
        "provider_flags_changed": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    if (root / "manifest.json").exists() and json.loads((root / "manifest.json").read_bytes()).get(
        "resume_scan_identity"
    ) != result["identity"]:
        raise BoundedError("RESUME_IMAGE_RECOMPUTED_MANIFEST_CHANGED")
    return result


def seal_image(root: Path, receipt: dict[str, Any], *, code_revision: str) -> dict[str, Any]:
    from .base23_delivery import PURPOSE
    from .base23_native_image import KIND as NATIVE_KIND

    native = receipt.get("native_receipt", {})
    if (
        (root / "manifest.json").exists()
        or (root / "READY").exists()
        or receipt.get("kind") != KIND
        or receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
        or native.get("identity") != digest({k: v for k, v in native.items() if k != "identity"})
        or native.get("kind") != NATIVE_KIND
        or not native.get("assessment", {}).get("build_eligible")
        or native.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
    ):
        raise BoundedError("RESUME_IMAGE_UNSEALED_FULL_ACCEPTANCE_REQUIRED")
    body = {
        "kind": NATIVE_KIND,
        "purpose": PURPOSE,
        "version": "2.3.1",
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "materialization_sha256": native["materialization_sha256"],
        "scan_identity": native["identity"],
        "resume_scan_identity": receipt["identity"],
        "code_revision": code_revision,
        "assessment": native["assessment"],
        "summaries": native["summaries"],
        "runtime": {"pyarrow": native["pyarrow_version"]},
        "consumer_admission": "NOT_GRANTED",
        "independent_acceptance": "SEPARATE_RECEIPTS_REQUIRED",
    }
    body["content_digest"] = digest(body)
    (root / "manifest.json").open("xb").write(canonical(body))
    (root / "READY").open("x").write(file_sha(root / "manifest.json") + "\n")
    return body
