"""Self-contained financial weekly observation, separate from complete Weekly.

Only this domain's two identity reference tables and original source/receipts are
embedded. Archived source paths are never opened. Offline acceptance reproduces
all three typed tables under the same bounded engine in a fresh scratch root.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .base22 import canonical, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_financial import TABLES
from .base22_sources import _fingerprint, _tree, physical_path
from .base24_weekly_financial import CONTRACT, REFERENCE_TABLES, weekly_parent
from .base24_weekly_materialize import materialize_statements

KIND = "base24_weekly_original_financial_observation_v1"
SOURCE_NAMES = ("financial_statements.parquet", "financial_statements.parquet.receipts.parquet")


def _financial_control(path: Path, source_sha: str, envelopes: int, through: date) -> str:
    selected = [
        d
        for d in json.loads(path.read_bytes())["datasets"]
        if d.get("name") == "financial_statements"
    ]
    if len(selected) != 1:
        raise BoundedError("WEEKLY_FINANCIAL_DATASET_CONTROL_MISSING")
    row = selected[0]
    if (
        row.get("status") != "ready"
        or row.get("sha256") != source_sha
        or type(row.get("row_count")) is not int
        or row["row_count"] != envelopes
        or row.get("watermark") != through.isoformat()
        or not isinstance(row.get("source_revision"), str)
        or not row["source_revision"]
    ):
        raise BoundedError("WEEKLY_FINANCIAL_DATASET_CONTROL_DIFFERS")
    return str(row["source_revision"])


def _closure(root: Path) -> set[str]:
    materialization = json.loads(_member(root, "references/MATERIALIZATION.json").read_bytes())
    return (
        {"weekly-config.json", "sources/datasets.json"}
        | {"sources/" + n for n in SOURCE_NAMES}
        | {"references/" + n for n in ("manifest.json", "MATERIALIZATION.json", "READY")}
        | {"tables/" + t + ".parquet" for t in TABLES}
        | {
            "references/" + record["path"]
            for t in REFERENCE_TABLES
            for record in materialization["layout"][t]["files"]
        }
    )


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def _copy(source: Path, target: Path, expected: str, budget: Budget) -> None:
    source = physical_path(source)
    before = _fingerprint(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with source.open("rb") as src, target.open("xb") as dst:
        while block := src.read(1024**2):
            budget.check(reserve=len(block))
            h.update(block)
            dst.write(block)
    if "sha256:" + h.hexdigest() != expected or _fingerprint(source) != before:
        raise BoundedError("WEEKLY_COPY_FROZEN_SOURCE_CHANGED")


def _member(root: Path, name: str) -> Path:
    rel = PurePosixPath(name)
    if rel.is_absolute() or rel.as_posix() != name or ".." in rel.parts or name == ".":
        raise BoundedError("WEEKLY_MEMBER_PATH_INVALID")
    p = physical_path(root / name)
    if not p.is_file() or p.stat().st_nlink != 1:
        raise BoundedError("WEEKLY_MEMBER_MISSING_OR_NOT_INDEPENDENT")
    return p


def seal_financial_weekly(
    *,
    base: Path,
    config: dict[str, Any],
    source: Path,
    receipts: Path,
    source_sha256: str,
    receipt_sha256: str,
    source_control: Path,
    source_control_sha256: str,
    expected_envelopes: int,
    observed_at: datetime,
    through: date,
    code_revision: str,
    budget: Budget,
    output: Path,
) -> dict[str, Any]:
    """Build one new immutable domain package. READY is the last product write."""
    if re.fullmatch(r"[0-9a-f]{40}", code_revision) is None:
        raise BoundedError("WEEKLY_CODE_IDENTITY_INVALID")
    if file_sha(physical_path(source_control)) != source_control_sha256:
        raise BoundedError("WEEKLY_FINANCIAL_DATASET_CONTROL_HASH_DIFFERS")
    revision = _financial_control(source_control, source_sha256, expected_envelopes, through)
    parent, resolver = weekly_parent(base, config)
    output = budget.destination(output)
    if output.exists():
        raise BoundedError("WEEKLY_OUTPUT_EXISTS_PRESERVED")
    output.mkdir()
    for name, path, sha in [
        (SOURCE_NAMES[0], source, source_sha256),
        (SOURCE_NAMES[1], receipts, receipt_sha256),
        ("datasets.json", source_control, source_control_sha256),
    ]:
        _copy(path, output / "sources" / name, sha, budget)
    _write(output / "weekly-config.json", config)
    for name in ("manifest.json", "MATERIALIZATION.json", "READY"):
        p = base / name
        expected = {
            "manifest.json": config["layers"]["dwd"]["manifest_sha256"],
            "MATERIALIZATION.json": config["layers"]["dwd"]["materialization_sha256"],
        }.get(name, file_sha(p))
        _copy(p, output / "references" / name, expected, budget)
    materialization = json.loads((output / "references/MATERIALIZATION.json").read_bytes())
    for table in REFERENCE_TABLES:
        for record in materialization["layout"][table]["files"]:
            relative = record["path"]
            # The native reader already checked these exact safe physical paths.
            p = base / relative
            if p not in parent.files(table):
                raise BoundedError("WEEKLY_REFERENCE_COPY_NOT_QUALIFIED")
            _copy(p, output / "references" / relative, record["sha256"], budget)
    proof = materialize_statements(
        source=output / "sources" / SOURCE_NAMES[0],
        receipts=output / "sources" / SOURCE_NAMES[1],
        source_sha256=source_sha256,
        receipt_sha256=receipt_sha256,
        expected_envelopes=expected_envelopes,
        resolver=resolver,
        observed_at=observed_at,
        through=through,
        output=output / "tables",
        budget=budget,
        expected_source_revision=revision,
    )
    files = {
        n: {"sha256": file_sha(output / n), "bytes": (output / n).stat().st_size}
        for n in sorted(_tree(output))
    }
    if set(files) != _closure(output):
        raise BoundedError("WEEKLY_EXACT_DOMAIN_CLOSURE_DIFFERS")
    manifest = {
        "kind": KIND,
        "contract_id": CONTRACT,
        "code_revision": code_revision,
        "business_timezone": "Asia/Shanghai",
        "business_through": through.isoformat(),
        "governance_observed_at": observed_at.isoformat(),
        "source_envelopes": expected_envelopes,
        "source_root_archival": str(source.parent),
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
        "complete_weekly_admission": False,
        "historical_PIT_admission": False,
        "base_binding": dict(parent.binding),
        "projection": proof,
        "files": files,
    }
    _write(output / "manifest.json", manifest)
    sha = file_sha(output / "manifest.json")
    budget.check()
    with (output / "READY").open("x", encoding="ascii") as stream:
        stream.write(sha + "\n")
    return {"manifest_sha256": sha, "projection": proof, "output": str(output)}


def verify_financial_weekly(root: Path, *, manifest_sha256: str, budget: Budget) -> dict[str, Any]:
    """Full physical census and bounded source replay, with no original root access."""
    root = physical_path(root)
    mp, ready = _member(root, "manifest.json"), _member(root, "READY")
    if file_sha(mp) != manifest_sha256 or ready.read_text().strip() != manifest_sha256:
        raise BoundedError("WEEKLY_MANIFEST_OR_READY_DIFFERS")
    manifest = json.loads(mp.read_bytes())
    if (
        manifest.get("kind") != KIND
        or manifest.get("contract_id") != CONTRACT
        or manifest.get("business_timezone") != "Asia/Shanghai"
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or any(
            manifest.get(k) is not False
            for k in (
                "execution_authorized",
                "complete_weekly_admission",
                "historical_PIT_admission",
            )
        )
    ):
        raise BoundedError("WEEKLY_PURPOSE_OR_ADMISSION_INVALID")
    files = manifest["files"]
    if _tree(root) != set(files) | {"manifest.json", "READY"}:
        raise BoundedError("WEEKLY_PHYSICAL_FILE_SET_DIFFERS")
    for name, entry in files.items():
        p = _member(root, name)
        if p.stat().st_size != entry["bytes"] or file_sha(p) != entry["sha256"]:
            raise BoundedError("WEEKLY_PHYSICAL_MEMBER_HASH_DIFFERS")
        budget.check()
    config = json.loads(_member(root, "weekly-config.json").read_bytes())
    parent, resolver = weekly_parent(root / "references", config)
    if set(files) != _closure(root):
        raise BoundedError("WEEKLY_EXACT_DOMAIN_CLOSURE_DIFFERS")
    if dict(parent.binding) != manifest["base_binding"]:
        raise BoundedError("WEEKLY_REFERENCE_BINDING_DIFFERS")
    observed = datetime.fromisoformat(manifest["governance_observed_at"])
    through = date.fromisoformat(manifest["business_through"])
    revision = _financial_control(
        root / "sources/datasets.json",
        files["sources/" + SOURCE_NAMES[0]]["sha256"],
        manifest["source_envelopes"],
        through,
    )
    reproduced = materialize_statements(
        source=root / "sources" / SOURCE_NAMES[0],
        receipts=root / "sources" / SOURCE_NAMES[1],
        source_sha256=files["sources/" + SOURCE_NAMES[0]]["sha256"],
        receipt_sha256=files["sources/" + SOURCE_NAMES[1]]["sha256"],
        expected_envelopes=manifest["source_envelopes"],
        resolver=resolver,
        observed_at=observed,
        through=through,
        output=budget.root / "reproduced-tables",
        budget=budget,
        expected_source_revision=revision,
    )
    if reproduced != manifest["projection"]:
        raise BoundedError("WEEKLY_REPRODUCED_PROJECTION_DIFFERS")
    from .base23_native_image import typed_summary
    from .financial_reporting import financial_reporting_schemas

    for table, schema in financial_reporting_schemas().items():
        if table not in reproduced["summaries"]:
            raise BoundedError("WEEKLY_EXACT_FINANCIAL_TABLE_SET_DIFFERS")
        actual = typed_summary([root / "tables" / (table + ".parquet")], schema, check=budget.check)
        if actual != reproduced["summaries"][table]:
            raise BoundedError("WEEKLY_PHYSICAL_PROJECTION_SEMANTICS_DIFFERS")
    return {
        "status": "PASS_FINANCIAL_DOMAIN_ONLY",
        "manifest_sha256": manifest_sha256,
        "source_envelopes": reproduced["source_envelopes"],
        "summaries": reproduced["summaries"],
        "full_original_source_reproduction": True,
        "consumer_admission": "NOT_GRANTED",
        "complete_weekly_admission": False,
    }
