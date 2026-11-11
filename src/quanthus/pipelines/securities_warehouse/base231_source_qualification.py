"""Real-source admission for the two domain outputs, independent of replay.

Reproducing a projection from arbitrary rehashed inputs does not qualify those
inputs. This successor envelope additionally binds raw members and identity
parents to exact retained ODS/DWD identities and the verified supply catalog.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_sources import _tree, physical_path
from .base231_domain_pack import _local, audit_domain_pack, validate_inputs

KIND = "p10_base231_source_qualified_domain_bundle_v1"
ODS = "sha256:74d57fa5ce944a673a961f5f89ea58f497d3f3b288846841fbb7ab5d1fda3a2b"
DWD = "sha256:6991d0c9440d4117c3dfdd3375e2267d0e43174203fc834751c9476cd7598206"
CATALOG = "sha256:a5a32da1ee0e4e14236c926c41f11e5b46d21927e26ec987ab2b994cb1acf9c7"
CONTRACT = "sha256:970268a45e4c3886e35c7c7a97880b3c121804e47a6b95b9926e2602d75cabee"


def qualify_retained_inputs(root: Path) -> dict[str, Any]:
    root = physical_path(root)
    domain = _local(root, "domain")
    inventory = json.loads((domain / "INPUTS.json").read_bytes())
    validate_inputs(domain, inventory)
    pins = {
        "evidence/original-ods-manifest.json": ODS,
        "evidence/original-dwd-manifest.json": DWD,
        "evidence/SOURCE-CATALOG.json": CATALOG,
        inventory["domain_contract"]: CONTRACT,
    }
    if any(file_sha(_local(domain, p)) != sha for p, sha in pins.items()):
        raise BoundedError("BASE231_REAL_SOURCE_AUTHORITY_CHANGED")
    materialization = _local(root, "provenance/original-dwd-materialization.json")
    dwd_manifest = json.loads((domain / "evidence/original-dwd-manifest.json").read_bytes())
    if file_sha(materialization) != dwd_manifest["materialization_sha256"]:
        raise BoundedError("BASE231_REAL_PARENT_DESCRIPTOR_CHANGED")
    layout = json.loads(materialization.read_bytes())["layout"]
    for table, paths in inventory["parents"].items():
        original = layout[table]["files"]
        if len(paths) != len(original) or any(
            file_sha(_local(domain, path)) != item["sha256"]
            or _local(domain, path).stat().st_size != item["size_bytes"]
            for path, item in zip(paths, original, strict=True)
        ):
            raise BoundedError("BASE231_REAL_PARENT_NOT_RETAINED_BASE_MEMBER")
    records = json.loads((domain / "evidence/SOURCE-CATALOG.json").read_bytes())["records"]
    raw = {r["path"]: r for r in records
           if r["record_type"] == "file" and r["path"].endswith("/raw_f10.parquet")}
    selected = inventory["raw_members"]
    if (len(selected) != len(raw)
            or {r["original_path"] for r in selected} != set(raw)):
        raise BoundedError("BASE231_REAL_RAW_SOURCE_SET_INCOMPLETE")
    for member in selected:
        original = raw[member["original_path"]]
        if (member["sha256"] != original["sha256"]
                or member["original_manifest_sha256"] != original["source_manifest_sha256"]
                or member["source_revision"] != original.get(
                    "source_revision", original.get("rollup_source"))
                or file_sha(_local(domain, member["path"])) != original["sha256"]):
            raise BoundedError("BASE231_REAL_RAW_MEMBER_NOT_ORIGINAL_QUALIFIED_SOURCE")
    return {"status": "REAL_RETAINED_SOURCE_CLOSURE_PASS", "raw_members": len(raw),
            "ODS_manifest_sha256": ODS, "DWD_manifest_sha256": DWD,
            "domain_manifest_sha256": file_sha(domain / "manifest.json"),
            "provider_PIT_qualified": False, "transaction_equivalence_granted": False,
            "consumer_admission": "NOT_GRANTED", "whole_base_acceptance": False}


def seal_qualified_bundle(root: Path) -> dict[str, Any]:
    if (root / "READY").exists() or (root / "manifest.json").exists():
        raise BoundedError("BASE231_QUALIFIED_BUNDLE_ALREADY_SEALED")
    qualification = qualify_retained_inputs(root)
    members = [{"path": name, "bytes": (root / name).stat().st_size,
                "sha256": file_sha(root / name)} for name in sorted(_tree(root))]
    body = {"kind": KIND, "version": "2.3.1", "business_through": "2026-09-30",
            "business_timezone": "Asia/Shanghai", "qualification": qualification,
            "qualifier_sha256": file_sha(Path(__file__)), "members": members,
            "purpose": "QUALIFIED_LITERAL_DOMAINS_NOT_COMPLETE_BASE_RELEASE",
            "consumer_admission": "NOT_GRANTED", "whole_base_acceptance": False}
    body["identity"] = digest(body)
    (root / "manifest.json").open("xb").write(canonical(body))
    (root / "READY").open("xb").write(canonical({"manifest_sha256": file_sha(
        root / "manifest.json")}))
    return body


def audit_qualified_bundle(root: Path, budget: Budget) -> dict[str, Any]:
    root = physical_path(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if (manifest.get("kind") != KIND or manifest.get("version") != "2.3.1"
            or manifest.get("business_through") != "2026-09-30"
            or manifest.get("business_timezone") != "Asia/Shanghai"
            or manifest.get("purpose") != "QUALIFIED_LITERAL_DOMAINS_NOT_COMPLETE_BASE_RELEASE"
            or manifest.get("consumer_admission") != "NOT_GRANTED"
            or manifest.get("whole_base_acceptance") is not False
            or manifest.get("qualifier_sha256") != file_sha(Path(__file__))
            or manifest.get("identity") != digest(
                {k: v for k, v in manifest.items() if k != "identity"})
            or json.loads((root / "READY").read_bytes()) != {
                "manifest_sha256": file_sha(root / "manifest.json")}):
        raise BoundedError("BASE231_QUALIFIED_BUNDLE_SEAL_CHANGED")
    members = manifest["members"]
    if (len(members) != len({m["path"] for m in members}) or _tree(root)
            != {m["path"] for m in members} | {"manifest.json", "READY"}):
        raise BoundedError("BASE231_QUALIFIED_BUNDLE_PHYSICAL_SET_CHANGED")
    for item in members:
        path = _local(root, item["path"])
        if (not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]):
            raise BoundedError("BASE231_QUALIFIED_BUNDLE_PHYSICAL_BYTES_CHANGED")
    qualification = qualify_retained_inputs(root)
    if qualification != manifest["qualification"]:
        raise BoundedError("BASE231_QUALIFIED_BUNDLE_SOURCE_BINDING_CHANGED")
    replay = audit_domain_pack(root / "domain", budget)
    return {"status": "PASS_REAL_SOURCE_BOUND_DOMAIN_COLD_REPLAY",
            "identity": manifest["identity"], "source_qualification": qualification,
            "projection": replay, "whole_base_acceptance": False}
