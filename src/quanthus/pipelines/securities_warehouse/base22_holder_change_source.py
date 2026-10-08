"""Source-reported holder changes, separate from ranked-list comments.

Only complete physical change tables with explicit fields/units are projected.
Natural-key collisions remain held. The scope never grants consumer admission,
historical knowledge, a legacy source alias or a cross-provider event priority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, Inexact, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, _physical, canonical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_f10_ownership import _cells
from .base22_financial import _clock
from .base22_holder_literals import _CHANGE_HEADERS
from .base22_precision import decimal_profile
from .base22_sources import _tree
from .financial_reporting import (
    FinancialCompanyResolver,
    financial_company_resolver_from_listed_company_rows,
)
from .ownership_capital import _effective_start, _stable_holder_key, _stable_id

CONTRACT_ID = "p10-base22-complete-retained-holder-change-source-v1"
TABLE = "dwd_shareholding_change"
CONFIG = "base22-holder-change-source.v1.json"
REPLAY_MODULES = (
    "base22.py",
    "base22_august.py",
    "base22_bounded.py",
    "base22_f10_ownership.py",
    "base22_financial.py",
    "base22_holder_change_source.py",
    "base22_holder_literals.py",
    "base22_outputs.py",
    "base22_precision.py",
    "base22_sources.py",
    "daily_price_basis.py",
    "disclosure_event.py",
    "financial_reporting.py",
    "ownership_capital.py",
)
_QUANTITY = re.compile(r"(?P<n>[+-]?\d+(?:\.\d+)?)(?P<u>亿股?|万股?|股)?")
_RATIO = re.compile(r"[+-]?\d+(?:\.\d+)?%?")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _share_header(token: str) -> bool:
    return bool(re.fullmatch(r"(?:持股数)?\(股\)|股", token))


def _quantity(token: str, *, explicit_share_unit: bool, signed: bool) -> Decimal | None:
    if token in {"", "-", "--"}:
        return None
    match = _QUANTITY.fullmatch(token.replace(",", ""))
    if not match or not explicit_share_unit and not match["u"]:
        raise BoundedError("HOLDER_CHANGE_SOURCE_QUANTITY_UNIT_UNAVAILABLE")
    with localcontext() as context:
        context.prec = 64
        context.traps[Inexact] = True
        value = Decimal(match["n"]) * (
            Decimal(100000000)
            if match["u"] and match["u"].startswith("亿")
            else Decimal(10000)
            if match["u"] and match["u"].startswith("万")
            else Decimal(1)
        )
        if not signed and value < 0 or not decimal_profile(value, precision=38, scale=10)["fits"]:
            raise BoundedError("HOLDER_CHANGE_SOURCE_QUANTITY_RANGE_OR_PRECISION_INVALID")
        return value.quantize(Decimal("1e-10"))


def _ratio(token: str, *, explicit_percent_unit: bool, delta: bool) -> Decimal | None:
    if token in {"", "-", "--"}:
        return None
    if not _RATIO.fullmatch(token) or not explicit_percent_unit and not token.endswith("%"):
        raise BoundedError("HOLDER_CHANGE_SOURCE_RATIO_UNIT_UNAVAILABLE")
    value = Decimal(token.removesuffix("%"))
    if (
        not (-100 if delta else 0) <= value <= 100
        or not decimal_profile(value, precision=18, scale=12)["fits"]
    ):
        raise BoundedError("HOLDER_CHANGE_SOURCE_RATIO_RANGE_OR_PRECISION_INVALID")
    return value.quantize(Decimal("1e-12"))


def project_change_tables(
    raw: dict[str, Any],
    *,
    source_sha256: str,
    resolver: FinancialCompanyResolver,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """One bounded original category yields native atoms and explicit block holds."""
    content, code = raw.get("content"), raw.get("stock_code")
    if (
        raw.get("category") != "股东研究"
        or not isinstance(content, str)
        or not 0 < len(content.encode()) <= 1024**2
        or not isinstance(code, str)
        or not re.fullmatch(r"\d{6}", code)
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)
        or not raw.get("source")
        or not raw.get("source_revision")
    ):
        raise BoundedError("HOLDER_CHANGE_EXACT_RAW_CATEGORY_BINDING_INVALID")
    acquired = _clock(raw.get("acquired_at"))
    lines = content.splitlines()
    starts = [n for n, line in enumerate(lines) if tuple(_cells(line)) in _CHANGE_HEADERS]
    if len(starts) > 16:
        raise BoundedError("HOLDER_CHANGE_SOURCE_TABLE_COUNT_BOUND_INVALID")
    blocks = []
    candidates: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for index, start in enumerate(starts):
        bound = starts[index + 1] if index + 1 < len(starts) else len(lines)
        end = next(
            (n + 1 for n in range(start + 1, bound) if lines[n].strip().startswith("└")), bound
        )
        block, header = lines[start:end], _cells(lines[start])
        proof: dict[str, Any] = {
            "block_index": index,
            "original_block_sha256": digest("\n".join(block)),
            "exact_header": header,
        }
        pending: list[list[str]] = []
        units: list[str] | None = None
        try:
            if not block[-1].strip().startswith("└"):
                raise BoundedError("HOLDER_CHANGE_SOURCE_PHYSICAL_TABLE_INCOMPLETE")
            for line in block[1:-1]:
                cells = _cells(line)
                if not cells or not any(cells):
                    if line.strip() and not line.strip().startswith(("├", "┌", "│")):
                        raise BoundedError("HOLDER_CHANGE_SOURCE_NONEMPTY_ROW_UNACCOUNTED")
                    continue
                if len(cells) != 7:
                    raise BoundedError("HOLDER_CHANGE_SOURCE_NONEMPTY_ROW_UNACCOUNTED")
                if not cells[0] and not cells[1] and any("股" in c or "%" in c for c in cells[2:6]):
                    if units is not None or pending:
                        raise BoundedError("HOLDER_CHANGE_SOURCE_UNIT_HEADER_CONFLICT")
                    units = cells
                elif pending and not cells[0] and cells[1] and not any(cells[2:]):
                    pending[-1][1] += cells[1]
                elif _DAY.fullmatch(cells[0]):
                    pending.append(cells)
                else:
                    raise BoundedError("HOLDER_CHANGE_SOURCE_DATE_OR_NONEMPTY_ROW_UNACCOUNTED")
            if len(pending) > 2048:
                raise BoundedError("HOLDER_CHANGE_SOURCE_ROW_BOUND_INVALID")
            parsed: list[tuple[str, dict[str, Any], str]] = []
            for cells in pending:
                day = date.fromisoformat(cells[0])
                if day > THROUGH:
                    counts["excluded_after_cutoff_rows"] += 1
                    continue
                if day > acquired.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    raise BoundedError("HOLDER_CHANGE_FUTURE_OBSERVATION_STATUS_UNPROVED")
                if not cells[1] or not cells[6] or re.search(r"拟|计划|预计|意向", cells[6]):
                    raise BoundedError(
                        "HOLDER_CHANGE_SOURCE_ACTUAL_HOLDER_AND_CHANGE_KIND_REQUIRED"
                    )
                parent, reason = resolver.resolve_for_report(code, period_end=day)
                if parent is None:
                    raise BoundedError(reason)
                # Retain before quantity in the source atom proof; the native
                # model has no shares-before column. Do not derive a delta.
                _quantity(
                    cells[2],
                    explicit_share_unit=bool(units and _share_header(units[2])),
                    signed=False,
                )
                changed = _quantity(
                    cells[3],
                    explicit_share_unit=bool(units and _share_header(units[3])),
                    signed=True,
                )
                after = _quantity(
                    cells[4],
                    explicit_share_unit=bool(units and _share_header(units[4])),
                    signed=False,
                )
                ratio = _ratio(
                    cells[5],
                    explicit_percent_unit=bool(units and "%" in units[5]),
                    delta=header[5] == "变动比例",
                )
                natural = [raw["source"], code, day, cells[1], cells[6]]
                key = digest(natural)
                atom = digest({"header": header, "unit_header": units, "cells": cells})
                row = {
                    "record_source": "p10_retained_f10_holder_change_table",
                    "source_record_id": digest([code, atom]),
                    "source_revision": raw["source_revision"],
                    "source_artifact_sha256": source_sha256,
                    "knowledge_from": max(acquired, parent.knowledge_from),
                    "knowledge_to": None,
                    "effective_at": _effective_start(day),
                    "announced_at": None,
                    "shareholding_change_id": _stable_id("shareholding_change", CONTRACT_ID, key),
                    "company_id": parent.company_id,
                    "security_id": parent.security_id,
                    "shareholder_source_name": cells[1],
                    "shareholder_id": None,
                    "change_type": cells[6],
                    "share_change": changed,
                    "percentage_change": ratio if header[5] == "变动比例" else None,
                    "post_holding": after,
                    "post_percentage": ratio if header[5] == "变动后占" else None,
                    "disclosure_id": None,
                    "holder_match_key": _stable_holder_key(cells[1]),
                    "raw_share_change": cells[3],
                    "share_change_unit": "SHARE" if changed is not None else None,
                    "raw_percentage_change": cells[5] if header[5] == "变动比例" else None,
                    "percentage_change_unit": "SOURCE_DECLARED_PERCENT_CHANGE_BASIS_UNAVAILABLE"
                    if ratio is not None and header[5] == "变动比例"
                    else None,
                    "raw_post_holding": cells[4],
                    "post_holding_unit": "SHARE" if after is not None else None,
                    "raw_post_percentage": cells[5] if header[5] == "变动后占" else None,
                    "post_percentage_unit": (
                        "PERCENT_OF_SOURCE_TOTAL_SHARES"
                        if units and "总股本" in units[5]
                        else "SOURCE_DECLARED_POST_PERCENT_BASIS_UNAVAILABLE"
                    )
                    if ratio is not None and header[5] == "变动后占"
                    else None,
                    "parser_version": CONTRACT_ID,
                }
                parsed.append((key, row, atom))
            for key, row, atom in parsed:
                candidates[key].append((row, atom))
            proof.update(
                {
                    "status": "EXACT_COMPLETE_SOURCE_CHANGE_TABLE",
                    "source_data_rows": len(pending),
                    "typed_candidate_rows": len(parsed),
                    "source_atoms_sha256": digest([a for _, _, a in parsed]),
                }
            )
        except (BoundedError, ValueError) as error:
            counts["held_blocks"] += 1
            proof.update({"status": "HELD_NOT_EMPTY_COVERAGE", "reason": str(error)})
        blocks.append(proof)
    output = []
    for _key, values in sorted(candidates.items()):
        if len({atom for _, atom in values}) != 1:
            counts["held_natural_keys"] += 1
        else:
            output.append(values[0][0])
            counts["exact_duplicate_source_rows"] += len(values) - 1
    if not starts and re.search(r"【\d+\.股东持股变动】", content):
        counts["held_blocks"] += 1
    proof = {
        "contract_id": CONTRACT_ID,
        "raw_row_sha256": digest(raw),
        "content_sha256": raw["content_sha256"],
        "source_member_sha256": source_sha256,
        "actual_acquisition": acquired,
        "exact_change_tables": len(starts),
        "blocks": blocks,
        "typed_rows": len(output),
        "typed_rows_sha256": digest(output),
        "held_blocks": counts["held_blocks"],
        "held_natural_keys": counts["held_natural_keys"],
        "exact_duplicate_source_rows": counts["exact_duplicate_source_rows"],
        "excluded_after_cutoff_rows": counts["excluded_after_cutoff_rows"],
        "whole_change_scope": "HELD_NOT_EMPTY_COVERAGE"
        if counts["held_blocks"] or counts["held_natural_keys"]
        else "COMPLETE_SELECTED_CHANGE_TABLES"
        if starts
        else "NO_SELECTED_CHANGE_TABLE_IN_EXACT_RAW_CATEGORY",
        "date_or_quantity_inferred": False,
        "list_comments_are_change_events": False,
        "original_publication_unavailable": True,
        "historical_PIT_admission": False,
        "legacy_source_alias_inferred": False,
        "consumer_admission": "NOT_GRANTED",
        "full_native_version_union": "SEPARATE_GATE",
    }
    proof["identity"] = digest(proof)
    return {TABLE: output}, proof


def replay_change_sources(
    root: Path, body: dict[str, Any], budget: Budget, consume: Any
) -> dict[str, Any]:
    """Exactly one source parse per raw; share parent materialization once."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    _, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    parents = {}
    for name in ("dwd_security", "dwd_security_identifier"):
        items = body["parents"][name]
        if sum(i["rows"] for i in items) > 32768:
            raise BoundedError("HOLDER_CHANGE_PARENT_SCOPE_BOUND_INVALID")
        parent_rows = []
        for item in items:
            physical = pq.ParquetFile(_physical(root, item["path"], independent=True))
            if physical.metadata.num_rows != item["rows"] or not physical.schema_arrow.equals(
                ss[name], check_metadata=False
            ):
                raise BoundedError("HOLDER_CHANGE_PARENT_SCHEMA_OR_COUNT_CHANGED")
            parent_rows.extend(physical.read().to_pylist())
        parents[name] = parent_rows
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    counts: Counter[str] = Counter()
    held: Counter[str] = Counter()
    if len(body["raw_inputs"]) != 4 or {i["source_day"] for i in body["raw_inputs"]} != {
        "2026-08-01",
        "2026-08-08",
        "2026-08-22",
        "2026-08-29",
    }:
        raise BoundedError("HOLDER_CHANGE_FOUR_EXACT_RETAINED_WEEKS_REQUIRED")
    for item in body["raw_inputs"]:
        raw_path = _physical(root, item["path"], independent=True)
        if file_sha(raw_path) != item["sha256"]:
            raise BoundedError("HOLDER_CHANGE_ORIGINAL_RAW_MEMBER_CHANGED")
        physical = pq.ParquetFile(raw_path)
        if (
            not 0 < item["rows"] <= 10000
            or physical.metadata.num_rows != item["rows"]
            or raw_path.stat().st_size > 128 * 1024**2
        ):
            raise BoundedError("HOLDER_CHANGE_RAW_SCOPE_BOUND_OR_COUNT_CHANGED")
        seen = set()
        for batch in physical.iter_batches(16):
            budget.check()
            for raw in batch.to_pylist():
                if raw["category"] == "股本结构":
                    counts["capital_raw_reused_elsewhere"] += 1
                    continue
                if raw["category"] != "股东研究" or raw["stock_code"] in seen:
                    raise BoundedError("HOLDER_CHANGE_EXACT_CATEGORY_OR_UNIQUE_SECURITY_CHANGED")
                seen.add(raw["stock_code"])
                output, proof = project_change_tables(
                    raw, source_sha256=item["original_member_sha256"], resolver=resolver
                )
                counts["holder_raw_rows"] += 1
                counts["source_tables"] += proof["exact_change_tables"]
                counts["typed_rows"] += len(output[TABLE])
                for field in (
                    "held_blocks",
                    "held_natural_keys",
                    "exact_duplicate_source_rows",
                    "excluded_after_cutoff_rows",
                ):
                    counts[field] += proof[field]
                for block in proof["blocks"]:
                    if block["status"] != "EXACT_COMPLETE_SOURCE_CHANGE_TABLE":
                        held[block["reason"]] += 1
                consume(item, raw["stock_code"], output[TABLE], proof)
        if len(seen) != 4992:
            raise BoundedError("HOLDER_CHANGE_FROZEN_SECURITY_SELECTION_CHANGED")
    result = {
        "counts": dict(counts),
        "held_reason_counts": dict(held),
        "scope": "SOURCE_ATOMS_ONLY_NATIVE_VERSION_UNION_AND_COVERAGE_SEPARATE",
    }
    result["identity"] = digest(result)
    return result


def audit_change_source_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Full original raw replay, typed rows, global keys, time, lineage and parents."""
    import pyarrow.parquet as pq

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "COMPLETE_RETAINED_CHANGE_TABLE_SOURCE_ATOMS_NOT_NATIVE_UNION"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("code_closure", {})) != set(REPLAY_MODULES)
        or set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("HOLDER_CHANGE_OFFLINE_DESCRIPTOR_OR_PHYSICAL_SET_CHANGED")
    for name, sha in body["code_closure"].items():
        if file_sha(Path(__file__).with_name(name)) != sha:
            raise BoundedError("HOLDER_CHANGE_OFFLINE_CODE_CHANGED")
    for rel, member in body["physical_members"].items():
        physical = _physical(root, rel, independent=True)
        if (
            physical.stat().st_size != member["size_bytes"]
            or file_sha(physical) != member["sha256"]
        ):
            raise BoundedError("HOLDER_CHANGE_OFFLINE_MEMBER_CHANGED")
        budget.check()
    frozen_config = Path(__file__).resolve().parents[4] / "config/pipelines" / CONFIG
    if (root / "contract" / CONFIG).read_bytes() != frozen_config.read_bytes():
        raise BoundedError("HOLDER_CHANGE_OFFLINE_CONFIG_CHANGED")
    predecessor = json.loads((root / "evidence/predecessor-scope.json").read_bytes())
    if (
        predecessor["identity"] != digest({k: v for k, v in predecessor.items() if k != "identity"})
        or predecessor["identity"] != body["source_identity"]
        or body["raw_inputs"] != predecessor["raw_local_members"]
    ):
        raise BoundedError("HOLDER_CHANGE_OFFLINE_EXACT_SOURCE_SELECTION_CHANGED")
    registration = json.loads((root / "evidence/write-registration.json").read_bytes())
    if (
        registration["identity"]
        != digest({k: v for k, v in registration.items() if k != "identity"})
        or registration["identity"] != body["registration_identity"]
        or registration["source_identity"] != body["source_identity"]
    ):
        raise BoundedError("HOLDER_CHANGE_OFFLINE_WRITE_REGISTRATION_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    streams, proofs = {}, {}
    for day, item in body["outputs"].items():
        physical = pq.ParquetFile(root / item["path"])
        if (
            not physical.schema_arrow.equals(ss[TABLE], check_metadata=False)
            or physical.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("HOLDER_CHANGE_OFFLINE_TYPED_SCHEMA_OR_COUNT_CHANGED")
        streams[day] = (r for batch in physical.iter_batches(1024) for r in batch.to_pylist())
        proofs[day] = (root / item["proof_path"]).open("rb")

    admitted: set[tuple[str, str, str]] = set()

    def compare(
        item: dict[str, Any], code: str, expected: list[dict[str, Any]], proof: dict[str, Any]
    ) -> None:
        day = item["source_day"]
        try:
            recorded = json.loads(next(proofs[day]))
            actual = [next(streams[day]) for _ in expected]
        except (StopIteration, ValueError):
            raise BoundedError("HOLDER_CHANGE_OFFLINE_REPLAY_STREAM_TRUNCATED") from None
        if (
            recorded != {"stock_code": code, "proof": json.loads(canonical(proof))}
            or actual != expected
        ):
            raise BoundedError("HOLDER_CHANGE_OFFLINE_RAW_OR_TYPED_ATOMS_CHANGED")
        admitted.update(
            (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
            for r in expected
        )
        budget.check()

    try:
        replay = replay_change_sources(root, body, budget, compare)
        sentinel = object()
        if (
            any(next(s, sentinel) is not sentinel for s in streams.values())
            or any(next(s, sentinel) is not sentinel for s in proofs.values())
            or replay != body["replay_summary"]
        ):
            raise BoundedError("HOLDER_CHANGE_OFFLINE_EXTRA_ROWS_OR_SCOPE_CHANGED")
    finally:
        for stream in proofs.values():
            stream.close()
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        paths = [root / i["path"] for i in body["outputs"].values()]
        scan.add(TABLE, paths)
        rule = contract["tables"][TABLE]
        scan.keys(TABLE, rule["business_key"], rule["version_key"])
        scan.row_rules(TABLE, ss[TABLE], rule)
        scan.lineage(TABLE, sorted(admitted))
        for parent, items in body["parents"].items():
            scan.add(parent, [root / i["path"] for i in items])
        for parent, field in (
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ):
            scan.reject(
                f"SELECT 1 FROM {TABLE} c WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{field}=c.{field}) LIMIT 1",
                "HOLDER_CHANGE_OFFLINE_PARENT_CLOSURE_FAILED",
            )
    return {
        "status": "ALL_LOCAL_RAW_CHANGE_GLOBAL_SCHEMA_TIME_PARENT_REPLAY_PASS_NOT_NATIVE_UNION",
        "scope_identity": body["identity"],
        "typed_rows": replay["counts"]["typed_rows"],
        "raw_rows": replay["counts"]["holder_raw_rows"],
        "consumer_admission": "NOT_GRANTED",
        "complete_Base22": False,
    }
