"""Literal, complete dated holder matrices with explicit share-nature components.

This candidate profile keeps the native physical schema and changes the scoped
business key only. Blank-name numeric rows belong to the immediately preceding
named group within a complete matrix, and only for distinct explicit share-nature
labels. They never acquire a fabricated holder rank or an aggregated quantity.
Incomplete/unknown matrices are separately held, not admitted as empty coverage.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import CONTRACT_SHA, THROUGH, canonical, digest, file_sha, load_contract
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_financial import _clock
from .base22_holder_column_revision import _KEYS
from .base22_holder_column_revision import TABLE as TABLE
from .base22_holder_literals import _holder_view
from .base22_precision import decimal_profile
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import _effective_start, _stable_holder_key, ownership_capital_schemas

CONTRACT_ID = "p10-base22-literal-holder-components-v1"
CONTRACT_FILE = "base22-holder-component-grain.v1.json"
COMPONENT_KEYS = _KEYS + ["share_nature"]
_DATE = re.compile(r"截至日期：(\d{4}-\d{2}-\d{2})\s+十大(流通)?股东情况")
_QUANTITY = re.compile(r"(?P<n>\d+(?:\.\d+)?)(?P<u>亿股?|万股?|股)?")
_PERCENT = re.compile(r"(?P<n>\d+(?:\.\d+)?)%?(?P<b>\s+占[ABH]股)?")
_COMPONENTS = {limit + market + "股" for limit in ("限售", "无限售") for market in "ABH"}
_HEADERS = {
    ("股东名称", "持股数(股)", ratio, nature, "增减情况(股)")
    for ratio in ("占总股本比(%)", "占流通股比(%)", "占总流通股比(%)")
    for nature in ("股份性质", "股东性质")
} | {("股东名称", "持股数量", "占总股本", "股份性质")}


def load_component_contract(config_root: Path) -> dict[str, Any]:
    """Opt in explicitly; frozen v1, all other table rules and schemas stay fixed."""
    native, _, _ = load_contract(config_root)
    path = config_root / CONTRACT_FILE
    import json

    rule: dict[str, Any] = json.loads(path.read_bytes())
    expected = {
        "schema_version": 1,
        "contract_id": CONTRACT_ID,
        "scope": "candidate_intermediate_only",
        "table": TABLE,
        "base_contract_sha256": CONTRACT_SHA,
        "business_key": COMPONENT_KEYS,
        "version_key": COMPONENT_KEYS + ["knowledge_from"],
        "physical_schema": "unchanged_pinned_v1_1",
        "nullable_component": "canonical_null_for_unsplit_or_floating_source_row",
        "component_labels": sorted(_COMPONENTS),
        "ratio_basis_labels": ["占A股", "占B股", "占H股"],
        "rank_rule": "named_holder_group_ordinal_not_physical_component_ordinal",
        "revision_rule": "new_governance_complete_group_close_old_open_literal_components",
        "consumer_admission": "NOT_GRANTED",
        "full_image_integration": "SEPARATE_GATE",
    }
    if rule != expected or native["tables"][TABLE]["business_key"] != _KEYS:
        raise BoundedError("HOLDER_COMPONENT_FROZEN_CONTRACT_INVALID")
    return rule


def component_key(row: dict[str, Any]) -> str:
    return digest([row[k] for k in COMPONENT_KEYS])


def _scalar(quantity: str, percentage: str) -> tuple[Decimal, Decimal | None, str]:
    q = _QUANTITY.fullmatch(quantity.replace(",", ""))
    p = _PERCENT.fullmatch(percentage)
    if q is None or (percentage not in {"", "-"} and p is None):
        raise BoundedError("HOLDER_COMPONENT_LITERAL_UNIT_UNQUALIFIED")
    with localcontext() as context:
        context.prec = 64
        value = Decimal(q["n"]) * (
            Decimal(100000000)
            if q["u"] and q["u"].startswith("亿")
            else Decimal(10000)
            if q["u"] and q["u"].startswith("万")
            else Decimal(1)
        )
        ratio = Decimal(p["n"]) if p else None
    if not decimal_profile(value, precision=38, scale=10)["fits"] or (
        ratio is not None
        and (not 0 <= ratio <= 100 or not decimal_profile(ratio, precision=18, scale=12)["fits"])
    ):
        raise BoundedError("HOLDER_COMPONENT_EXACT_PRECISION_OR_RANGE_INVALID")
    basis = (
        p["b"].strip()
        if p and p["b"]
        else "SOURCE_DENOMINATOR_NOT_INFERRED"
        if p
        else "EXPLICIT_NULL"
    )
    return value, ratio, basis


def _matrix(lines: list[str], *, floating: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """No row may cross a holder, table, date or explicit closing boundary."""
    if not lines or not lines[-1].strip().startswith("└"):
        raise BoundedError("HOLDER_COMPONENT_MATRIX_INCOMPLETE")
    header: list[str] | None = None
    groups: list[list[dict[str, Any]]] = []
    scalar_ledger: list[list[str]] = []
    comments: list[str] = []
    wraps = components = 0
    previous_numeric = False
    for number, line in enumerate(lines[1:-1], 1):
        cells = _cells(line)
        if cells and cells[0] == "股东名称":
            if header is not None or tuple(cells) not in _HEADERS:
                raise BoundedError("HOLDER_COMPONENT_HEADER_UNQUALIFIED")
            header = cells
            continue
        if not cells or not any(cells):
            if header is not None and line.strip() and not line.strip().startswith(("├", "┌", "│")):
                raise BoundedError("HOLDER_COMPONENT_NONEMPTY_ROW_UNACCOUNTED")
            # A separator closes adjacency; empty layout cells do not name a holder.
            if groups and line.strip().startswith(("├", "┌", "└")):
                previous_numeric = False
            continue
        if header is None:
            raise BoundedError("HOLDER_COMPONENT_NONEMPTY_ROW_UNACCOUNTED")
        numeric = len(cells) == len(header) and _QUANTITY.fullmatch(cells[1].replace(",", ""))
        if numeric:
            if cells[0]:
                groups.append([])
            elif (
                floating
                or header[3] != "股份性质"
                or not groups
                or not previous_numeric
                or cells[3] not in _COMPONENTS
                or any(r["classification"] not in _COMPONENTS for r in groups[-1])
                or cells[3] in {r["classification"] for r in groups[-1]}
            ):
                raise BoundedError("HOLDER_COMPONENT_EMPTY_NAME_SCOPE_UNPROVEN")
            else:
                components += 1
            value, ratio, basis = _scalar(cells[1], cells[2])
            name = cells[0] or groups[-1][0]["name"]
            groups[-1].append(
                {
                    "name": name,
                    "classification": cells[3] or None,
                    "quantity": value,
                    "percentage": ratio,
                    "raw_quantity": cells[1],
                    "raw_percentage": cells[2],
                    "basis": basis,
                    "physical_row": number,
                }
            )
            scalar_ledger.append([cells[1], cells[2]])
            if len(header) == 5 and cells[4]:
                comments.append(digest(cells[4]))
            previous_numeric = True
        elif groups and cells[0] and not any(cells[1:]):
            for row in groups[-1]:
                row["name"] += cells[0]
            wraps += 1
            previous_numeric = False
        elif (
            groups
            and len(cells) == len(header)
            and not any(cells[1:3])
            and cells[3]
            and not any(cells[4:])
            and re.fullmatch(r"[A-Za-z\u4e00-\u9fff -]+", cells[3])
        ):
            for row in groups[-1]:
                row["name"] += cells[0]
            groups[-1][-1]["classification"] = (groups[-1][-1]["classification"] or "") + cells[3]
            wraps += 1
            previous_numeric = False
        else:
            raise BoundedError("HOLDER_COMPONENT_NONEMPTY_ROW_UNACCOUNTED")
    if header is None or not groups or len(groups) > 10:
        raise BoundedError("HOLDER_COMPONENT_COMPLETE_HEADER_OR_RANK_BOUND_INVALID")
    rows = []
    for rank, group in enumerate(groups, 1):
        if len(group) > 1 and (
            len({r["classification"] for r in group}) != len(group)
            or any(r["classification"] not in _COMPONENTS for r in group)
        ):
            raise BoundedError("HOLDER_COMPONENT_DISTINCT_LITERAL_CLASS_CHANGED")
        for row in group:
            rows.append({**row, "rank": rank})
    if scalar_ledger != [[r["raw_quantity"], r["raw_percentage"]] for r in rows]:
        raise BoundedError("HOLDER_COMPONENT_SCALAR_LEDGER_CHANGED")
    return rows, {
        "header": header,
        "named_holder_groups": len(groups),
        "numeric_rows": len(rows),
        "blank_name_components": components,
        "literal_text_wraps": wraps,
        "ordered_scalar_tokens_sha256": digest(scalar_ledger),
        "increment_comments": {
            "cells": len(comments),
            "sha256": digest(comments),
            "qualification": "NOT_GRANTED",
        },
    }


def project_holder_components(
    raw: dict[str, Any],
    *,
    source_sha256: str,
    resolver: FinancialCompanyResolver,
    contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Parse each matrix once; qualified rows and held matrices share one census."""
    import pyarrow as pa  # type: ignore[import-untyped]

    content = raw.get("content")
    if (
        contract.get("contract_id") != CONTRACT_ID
        or contract.get("business_key") != COMPONENT_KEYS
        or raw.get("category") != "股东研究"
        or not isinstance(content, str)
        or not 0 < len(content.encode()) <= 1024**2
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or not isinstance(raw.get("stock_code"), str)
        or not re.fullmatch(r"\d{6}", raw["stock_code"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)
        or not raw.get("source")
        or not raw.get("source_revision")
    ):
        raise BoundedError("HOLDER_COMPONENT_RAW_OR_CONTRACT_BINDING_INVALID")
    acquisition = _clock(raw.get("acquired_at"))
    _, separate = _holder_view(content)
    # The predecessor view inserts a synthetic closing marker at an unrelated
    # change header. It is diagnostic only here: completeness needs the actual
    # original physical closing border, not a parser-view marker.
    lines = content.splitlines()
    starts = [n for n, line in enumerate(lines) if _DATE.search(line)]
    if not starts or len(starts) > 256:
        raise BoundedError("HOLDER_COMPONENT_NO_DATED_MATRIX_OR_BOUND_EXCEEDED")
    rows: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    bases: Counter[str] = Counter()
    seen = set()
    for index, start in enumerate(starts):
        bound = starts[index + 1] if index + 1 < len(starts) else len(lines)
        end = next(
            (n + 1 for n in range(start + 1, bound) if lines[n].strip().startswith("└")), bound
        )
        block = lines[start:end]
        match = _DATE.search(lines[start])
        assert match is not None
        frame = {
            "frame_index": index,
            "source_block_sha256": digest("\n".join(block)),
            "typed_rows": 0,
        }
        try:
            day = date.fromisoformat(match[1])
            items, census = _matrix(block, floating=bool(match[2]))
            if day > THROUGH:
                raise BoundedError("AFTER_IMAGE_CUTOFF")
            parent, reason = resolver.resolve_for_report(raw["stock_code"], period_end=day)
            if parent is None:
                raise BoundedError(reason)
            kind = "TOP_FLOATING_SHAREHOLDERS" if match[2] else "TOP_SHAREHOLDERS"
            projected: list[dict[str, Any]] = []
            for item in items:
                row = {
                    "record_source": raw["source"],
                    "source_record_id": (
                        f"stock_code={raw['stock_code']}&category=股东研究"
                        f"&content_sha256={raw['content_sha256']}"
                    ),
                    "source_revision": raw["source_revision"],
                    "source_artifact_sha256": source_sha256,
                    "knowledge_from": max(acquisition, parent.knowledge_from),
                    "knowledge_to": None,
                    "observation_start": _effective_start(day),
                    "observation_end": None,
                    "company_id": parent.company_id,
                    "security_id": parent.security_id,
                    "shareholder_id": None,
                    "shareholder_source_name": item["name"],
                    "holder_match_key": _stable_holder_key(item["name"]),
                    "holder_rank": item["rank"],
                    "table_kind": kind,
                    "holding_type": kind,
                    "share_nature": None if match[2] else item["classification"],
                    "holder_category": item["classification"] if match[2] else None,
                    "holding_quantity": item["quantity"],
                    "holding_percentage": item["percentage"],
                    "raw_holding_quantity": item["raw_quantity"],
                    "raw_holding_percentage": item["raw_percentage"],
                    "holding_quantity_unit": "亿股"
                    if "亿" in item["raw_quantity"]
                    else "万股"
                    if "万" in item["raw_quantity"]
                    else "股",
                    "holding_percentage_unit": "PERCENT"
                    if item["percentage"] is not None
                    else None,
                    "parser_version": CONTRACT_ID,
                }
                key = component_key(row)
                if key in seen or key in {component_key(r) for r in projected}:
                    raise BoundedError("HOLDER_COMPONENT_DUPLICATE_GLOBAL_KEY")
                projected.append(row)
            for row, item in zip(projected, items, strict=True):
                seen.add(component_key(row))
                bases[item["basis"]] += 1
            rows.extend(projected)
            frame.update(
                {
                    "status": "QUALIFIED_COMPLETE_MATRIX",
                    "business_date": day.isoformat(),
                    "holding_type": kind,
                    "typed_rows": len(projected),
                    "census": census,
                    "ordered_typed_rows_sha256": digest(projected),
                }
            )
        except (BoundedError, ValueError) as error:
            frame.update({"status": "HELD_NOT_EMPTY_COVERAGE", "reason": str(error)})
        frames.append(frame)
    typed = pa.Table.from_pylist(rows, schema=ownership_capital_schemas()[TABLE]).to_pylist()
    if typed != rows:
        raise BoundedError("HOLDER_COMPONENT_TYPED_ATOM_OR_CLOCK_CHANGED")
    proof = {
        "contract_id": CONTRACT_ID,
        "contract_sha256": digest(contract),
        "raw_row_sha256": digest(raw),
        "original_content_sha256": raw["content_sha256"],
        "source_member_sha256": source_sha256,
        "actual_acquisition": acquisition,
        "original_acquisition": raw["acquired_at"],
        "frames": frames,
        "percentage_basis_counts": dict(bases),
        "typed_rows_sha256": digest(typed),
        "separate_nonempty_domains": separate,
        "shared_parses_built": 1,
        "whole_category": "HELD"
        if any(f["status"] != "QUALIFIED_COMPLETE_MATRIX" for f in frames) or separate
        else "COMPLETE_DATED_HOLDER_MATRICES_ONLY",
        "source_numerals_or_denominators_rewritten": False,
        "consumer_admission": "NOT_GRANTED",
        "full_image_integration": "SEPARATE_GATE",
        "historical_PIT_admission": False,
    }
    proof["identity"] = digest(proof)
    return {TABLE: typed}, proof


def check_component_replay(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Compare complete raw/frame/key/time evidence, not a row-count assertion."""
    if canonical(expected) != canonical(actual):
        raise BoundedError("HOLDER_COMPONENT_OFFLINE_RAW_FRAME_REPLAY_CHANGED")


def qualify_component_group(
    before: list[dict[str, Any]],
    *,
    original_raw: dict[str, Any],
    original_member_sha256: str,
    later_raw: list[tuple[str, dict[str, Any]]],
    resolver: FinancialCompanyResolver,
    contract: dict[str, Any],
    observed_at: Any,
) -> dict[str, Any]:
    """Close the exact old group and open literal latest components at real now.

    A classification key move is a complete group rewrite, not an assertion that
    the mutable share-nature label identifies a continuous historical position.
    Later complete-frame omission or a held target frame never means zero holding.
    """
    import pyarrow as pa

    from .base22_holder_chronology import _native_origin

    observed = _clock(observed_at)
    if not 0 < len(before) <= 6 or not 0 < len(later_raw) <= 4:
        raise BoundedError("HOLDER_COMPONENT_GROUP_SCOPE_BOUND_INVALID")
    keys = {digest([r[k] for k in _KEYS]) for r in before}
    if len(keys) != 1 or any(
        r["knowledge_to"] is not None or _clock(r["knowledge_from"]) >= observed for r in before
    ):
        raise BoundedError("HOLDER_COMPONENT_GROUP_NOT_ONE_EXACT_OPEN_NATIVE_GROUP")
    native = _native_origin(original_raw, original_member_sha256, resolver)
    original = native.get(next(iter(keys)))
    # Existing legacy origin has one row per holder; do not silently replace a
    # previously qualified multi-component group using this migration contract.
    if len(before) != 1 or original != before[0]:
        raise BoundedError("HOLDER_COMPONENT_EXACT_NATIVE_ORIGINAL_BINDING_FAILED")
    original_clock = _clock(original_raw["acquired_at"])
    target = before[0]
    target_day = (
        target["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    )
    projected = []
    proofs = []
    clocks: dict[Any, str] = {}
    for sha, raw in later_raw:
        instant = _clock(raw.get("acquired_at"))
        if (
            raw.get("source") != original_raw["source"]
            or raw.get("stock_code") != original_raw["stock_code"]
            or not original_clock < instant < observed
        ):
            raise BoundedError("HOLDER_COMPONENT_SAME_PROVIDER_OR_ACTUAL_CHRONOLOGY_INVALID")
        if instant in clocks and clocks[instant] != digest(raw):
            raise BoundedError("HOLDER_COMPONENT_EQUAL_CLOCK_CONFLICT")
        clocks[instant] = digest(raw)
        values, proof = project_holder_components(
            raw, source_sha256=sha, resolver=resolver, contract=contract
        )
        matched = [
            f
            for f in proof["frames"]
            if f.get("business_date") == target_day
            and f.get("holding_type") == target["holding_type"]
        ]
        if len(matched) != 1 or matched[0]["status"] != "QUALIFIED_COMPLETE_MATRIX":
            raise BoundedError("HOLDER_COMPONENT_TARGET_FRAME_UNQUALIFIED_OR_MISSING")
        group = [r for r in values[TABLE] if digest([r[k] for k in _KEYS]) in keys]
        if not group:
            raise BoundedError("HOLDER_COMPONENT_LATER_FRAME_OMISSION_NOT_ZERO_OR_EXIT")
        projected.append((instant, digest(raw), group))
        proofs.append(proof["identity"])
    latest = max(projected, key=lambda item: (item[0], item[1]))
    closure = {
        "contract_id": CONTRACT_ID,
        "contract_sha256": digest(contract),
        "observed_at": observed,
        "before_rows_sha256": digest(before),
        "original_raw_sha256": digest(original_raw),
        "original_member_sha256": original_member_sha256,
        "retained_raw_proof_identities": sorted(proofs),
        "latest_actual_acquisition": latest[0],
        "latest_source_rows_sha256": digest(latest[2]),
        "evaluator_sha256": code_identity(),
    }
    identity = digest(closure)
    output = [{**r, "knowledge_to": observed} for r in before] + [
        {
            **r,
            "knowledge_from": observed,
            "knowledge_to": None,
            "source_artifact_sha256": identity,
            "source_revision": CONTRACT_ID + ":" + identity[7:],
        }
        for r in latest[2]
    ]
    if (
        pa.Table.from_pylist(output, schema=ownership_capital_schemas()[TABLE]).to_pylist()
        != output
    ):
        raise BoundedError("HOLDER_COMPONENT_GROUP_SERIALIZATION_CHANGED")
    return {
        "identity": identity,
        "input_closure": closure,
        "output": output,
        "old_atoms_preserved_except_knowledge_to": True,
        "new_real_governance_clock": observed,
        "classification_key_move_rule": "CLOSE_WHOLE_OLD_GROUP_OPEN_LITERAL_NEW_GROUP",
        "provider_acquisition_preserved_in_input_closure": True,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }


def code_identity() -> str:
    return file_sha(Path(__file__))
