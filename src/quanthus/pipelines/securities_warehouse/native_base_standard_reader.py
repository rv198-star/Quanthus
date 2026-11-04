"""Read sealed standard Base parents for isolated Daily data governance only.

Native scan and source-replay identities are explicitly named bootstrap proofs;
they are not independent A/B/C or trading/research acceptance receipts.
Legacy Base readers retain their original acceptance contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base22 import digest
from .base24_delivery import INTERVALS, ONE_MINUTE_DISPOSITION
from .base_build_inputs import GENERIC_KINDS, file_sha, identity, member
from .native_base23_reader import (
    PARENT_TABLES,
    NativeBase23,
    NativeBase23Error,
    _read_native_tables,
)

FORMAT = "native_standard_base"
SCHEMA = "quanthus.standard-base-daily-governance.v1"
CONTRACT = "p10-standard-base-daily-parent-bootstrap-v1"
STATUS = "SOURCE_AUDITED_GOVERNANCE_BOOTSTRAP_ONLY"


def _object(path: Path) -> dict[str, Any]:
    import json

    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise NativeBase23Error("standard Base parent metadata is not an object")
    return value


def _proof(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from .base_build_publication import CONTRACT as PAIR
    from .base_build_publication import _require_audit, portable_audit_identity

    manifest = _object(member(root, "manifest.json"))
    contract = _object(member(root, "contract/base-build-delivery.v1.json"))
    proof = manifest.get("paired_audit", {})
    _require_audit(proof, contract, manifest.get("code_revision"))
    gate = manifest.get("pairing", {})
    content = manifest.get("content", {})
    if (
        manifest.get("kind") != GENERIC_KINDS["dwd"]
        or manifest.get("version") != contract["base_version"]
        or manifest.get("business_through") != contract["business_through"]
        or content.get("role") != "dwd"
        or content.get("base_version") != contract["base_version"]
        or content.get("through_date") != contract["business_through"]
        or manifest.get("content_digest")
        != digest({k: v for k, v in manifest.items() if k != "content_digest"})
        or member(root, "READY").read_text().strip() != file_sha(root / "manifest.json")
        or manifest.get("scan_identity") != proof["DWD"]["identity"]
        or manifest.get("materialization_sha256") != proof["DWD"]["materialization_sha256"]
        or manifest["materialization_sha256"] != file_sha(member(root, "MATERIALIZATION.json"))
        or gate.get("contract") != PAIR
        or gate.get("identity") != identity({k: v for k, v in gate.items() if k != "identity"})
        or gate.get("binding") != proof["binding"]
        or gate.get("historical_gate") != proof["historical_gate"]
        or gate.get("audit_content_identity") != portable_audit_identity(proof)
        or gate.get("source_replay_identity") != proof["replay_identity"]
        or gate.get("quality_held_added") != 0
        or manifest.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise NativeBase23Error("standard Base paired source proof differs")
    return manifest, {
        "contract": CONTRACT,
        "status": STATUS,
        "tables": list(PARENT_TABLES),
        "native_scan_identity": manifest["scan_identity"],
        "source_replay_identity": gate["source_replay_identity"],
        "pair_registration_identity": gate["identity"],
        "trading_or_research_admission": False,
    }


def governance_config(root: Path, *, build_id: str) -> dict[str, Any]:
    manifest, proof = _proof(root)
    return {
        "schema_version": SCHEMA,
        "build_id": build_id,
        "purpose": "isolated_daily_governance",
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
        "through_date": manifest["business_through"],
        "standard_parent_reference": proof,
        "layers": {
            "dwd": {
                "format": FORMAT,
                "package_id": "standard-base-"
                + manifest["version"]
                + "-"
                + file_sha(root / "manifest.json")[7:19],
                "manifest_sha256": file_sha(root / "manifest.json"),
                "content_digest": manifest["content_digest"],
                "materialization_sha256": manifest["materialization_sha256"],
            }
        },
    }


def require_daily_core_governance(config: Mapping[str, Any]) -> None:
    proof = config.get("standard_parent_reference", {})
    if (
        config.get("schema_version") != SCHEMA
        or config.get("purpose") != "isolated_daily_governance"
        or config.get("consumer_admission") != "NOT_GRANTED"
        or config.get("execution_authorized") is not False
        or config.get("layers", {}).get("dwd", {}).get("format") != FORMAT
        or set(proof)
        != {
            "contract",
            "status",
            "tables",
            "native_scan_identity",
            "source_replay_identity",
            "pair_registration_identity",
            "trading_or_research_admission",
        }
        or proof.get("contract") != CONTRACT
        or proof.get("status") != STATUS
        or proof.get("tables") != list(PARENT_TABLES)
        or proof.get("trading_or_research_admission") is not False
    ):
        raise NativeBase23Error("standard Daily governance bootstrap contract differs")
    import re

    if any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", str(proof[k]))
        for k in ("native_scan_identity", "source_replay_identity", "pair_registration_identity")
    ):
        raise NativeBase23Error("standard Daily bootstrap proof identities differ")


def read_native_tables(
    root: Path,
    config: Mapping[str, Any],
    tables: tuple[str, ...],
    *,
    relocated_members: Mapping[str, Path] | None = None,
) -> NativeBase23:
    require_daily_core_governance(config)
    manifest, expected = _proof(root)
    if config["standard_parent_reference"] != expected:
        raise NativeBase23Error("standard Daily parent proof binding differs")
    return _read_native_tables(
        root,
        config,
        tables,
        profile=(GENERIC_KINDS["dwd"], manifest["version"], manifest["purpose"], FORMAT),
        independent_files=True,
        default_intervals=INTERVALS,
        one_minute_disposition=ONE_MINUTE_DISPOSITION,
        relocated_members=relocated_members,
    )


def read_native_parent_references(root: Path, config: Mapping[str, Any]) -> NativeBase23:
    return read_native_tables(root, config, PARENT_TABLES)
