"""Exact F10 financial summary literals under an explicit September patch contract.

Complete dated matrices add source-scoped report/fact observations. Original
display precision is not provider precision; a literal yuan label is not a CNY
claim. Only malformed frames/coordinates are held, independently of healthy ones.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_financial import TABLES, _clock
from .base22_outputs import financial_concept_versions
from .base22_precision import decimal_profile
from .base23_ownership_reconfirmation import RAW_FIELDS
from .financial_reporting import (
    FinancialCompanyResolver,
    _period_coordinate,
    _stable_id,
    financial_reporting_schemas,
)

CONTRACT_ID = "p10-base231-financial-ownership-literals-v1"
NAMESPACE = "tdx.f10.major_financial_indicators.literal.v1"
THROUGH = date(2026, 9, 30)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SCALAR = re.compile(r"(?P<n>[+-]?\d+(?:\.\d+)?)(?P<u>万|亿)?(?P<p>%)?\Z")


def load_contract(path: Path) -> dict[str, Any]:
    contract: dict[str, Any] = json.loads(path.read_bytes())
    if (
        contract.get("schema_version") != 1 or contract.get("contract_id") != CONTRACT_ID
        or contract.get("version") != "2.3.1"
        or contract.get("business_through") != THROUGH.isoformat()
        or contract.get("business_timezone") != "Asia/Shanghai"
        or contract.get("consumer_admission") != "NOT_GRANTED"
        or contract.get("provider_PIT_qualified") is not False
        or contract.get("original_visibility_unavailable") != 4173
        or contract.get("financial_section") != "主要财务指标"
        or contract.get("source") != "tdx.get_company_info_content"
        or contract.get("source_revision") != "quanthus-fundamentals-provider-v1"
        or not isinstance(contract.get("financial_metrics"), dict)
        or len(contract["financial_metrics"]) != 16
    ):
        raise BoundedError("BASE231_DOMAIN_CONTRACT_INVALID")
    for label, rule in contract["financial_metrics"].items():
        if (
            not isinstance(label, str) or not isinstance(rule, list) or len(rule) != 2
            or not re.fullmatch(r"[a-z_]+", rule[0])
            or rule[1] not in {"PERCENT", "SOURCE_LITERAL_YUAN", "SOURCE_LITERAL_YUAN_PER_SHARE"}
            or not label.endswith("(%)" if rule[1] == "PERCENT" else "(元)")
        ):
            raise BoundedError("BASE231_FINANCIAL_METRIC_UNIT_INVALID")
    if len({r[0] for r in contract["financial_metrics"].values()}) != 16:
        raise BoundedError("BASE231_FINANCIAL_METRIC_KEY_DUPLICATE")
    return contract


def financial_literal_identity(raw: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
    content = raw.get("content")
    if (
        set(raw) != RAW_FIELDS or raw.get("category") != "财务分析"
        or raw.get("source") != "tdx.get_company_info_content"
        or raw.get("source_revision") != "quanthus-fundamentals-provider-v1"
        or raw.get("security_id") != raw.get("stock_code")
        or not re.fullmatch(r"\d{6}", str(raw.get("stock_code")))
        or not isinstance(content, str) or not 0 < len(content.encode()) <= 1048576
        or raw.get("content_sha256") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        or type(raw.get("source_start")) is not int or raw["source_start"] < 0
        or type(raw.get("source_length")) is not int or raw["source_length"] < 0
        or not isinstance(raw.get("source_filename"), str) or not raw["source_filename"]
    ):
        raise BoundedError("FINANCIAL_SUMMARY_RAW_IDENTITY_INVALID")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    if date.fromisoformat(raw["market_date"]) > THROUGH or acquired > observed:
        raise BoundedError("FINANCIAL_SUMMARY_CAPTURE_AFTER_CUTOFF")
    return {"complete_raw_row_sha256": digest(raw), "raw_acquired_at": raw["acquired_at"],
            "actual_acquisition_instant": acquired,
            "provider_updated_date_literal": raw["provider_updated_date"],
            "content_sha256": raw["content_sha256"], "governance_observed_at": observed}


def _frames(content: str) -> list[list[str]]:
    lines = content.splitlines()
    starts = [n for n, line in enumerate(lines) if line.strip() == "【主要财务指标】"]
    if len(starts) != 1:
        raise BoundedError("FINANCIAL_SUMMARY_SECTION_UNQUALIFIED")
    begin = starts[0] + 1
    end = next((n for n in range(begin, len(lines))
                if lines[n].strip().startswith("【")), len(lines))
    section = lines[begin:end]
    matrices: list[list[str]] = []
    pending: list[str] | None = None
    for line in section:
        if line.strip().startswith("┌"):
            if pending is not None:
                matrices.append(pending)
            pending = [line]
        elif pending is not None:
            pending.append(line)
            if line.strip().startswith("└"):
                matrices.append(pending)
                pending = None
        elif _cells(line):
            matrices.append([line])  # Unbounded cells are explicitly held.
    if pending is not None:
        matrices.append(pending)
    if not matrices or len(matrices) > 16:
        raise BoundedError("FINANCIAL_SUMMARY_MATRIX_BOUND")
    return matrices


def _matrix(lines: list[str]) -> tuple[list[date], list[tuple[str, list[str]]]]:
    if not lines[0].strip().startswith("┌") or not lines[-1].strip().startswith("└"):
        raise BoundedError("FINANCIAL_SUMMARY_FRAME_INCOMPLETE")
    days: list[date] = []
    records: list[tuple[str, list[str]]] = []
    for line in lines[1:-1]:
        cells = _cells(line)
        if cells and not any(cells):
            if not days or len(cells) != len(days) + 1:
                raise BoundedError("FINANCIAL_SUMMARY_BLANK_ROW_WIDTH_INVALID")
            continue
        if not cells:
            if line.strip() and not line.strip().startswith("├"):
                raise BoundedError("FINANCIAL_SUMMARY_NONEMPTY_ROW_UNACCOUNTED")
            continue
        if cells[0] == "财务指标":
            if days or len(cells) < 2 or not all(_DAY.fullmatch(v) for v in cells[1:]):
                raise BoundedError("FINANCIAL_SUMMARY_DATE_HEADER_INVALID")
            days = [date.fromisoformat(v) for v in cells[1:]]
            if len(set(days)) != len(days) or len(days) > 16:
                raise BoundedError("FINANCIAL_SUMMARY_DATE_HEADER_INVALID")
        elif (days and len(cells) == len(days) + 1 and not cells[0]
              and records and records[-1][0] == "审计意见"):
            # Original opinion text wraps vertically under an empty label. It
            # remains an accounted unsupported field, never a numeric fact.
            label, opinion_values = records[-1]
            records[-1] = (label, [a + b for a, b in zip(opinion_values, cells[1:], strict=True)])
        elif not days or len(cells) != len(days) + 1 or not cells[0]:
            raise BoundedError("FINANCIAL_SUMMARY_NONEMPTY_ROW_UNACCOUNTED")
        elif records and not any(cells[1:]):
            # Source wraps the label below its value line; only an empty-value
            # continuation can extend it. Unit-bearing labels are already closed.
            previous, values = records[-1]
            if previous.endswith(("(元)", "(%)")):
                records.append((cells[0], cells[1:]))
            else:
                records[-1] = (previous + cells[0], values)
        else:
            records.append((cells[0], cells[1:]))
    if not days or not records or len(records) > 128:
        raise BoundedError("FINANCIAL_SUMMARY_MATRIX_EMPTY_OR_BOUND")
    return days, records


def _scalar(token: str, unit: str) -> tuple[Decimal | None, str | None, bool, str]:
    if not token.strip():
        return None, None, True, "NULL"
    match = _SCALAR.fullmatch(token.replace(",", ""))
    if match is None or match["p"] and unit != "PERCENT":
        return None, token, False, "SOURCE_UNAVAILABLE" if token in {"-", "--", "—"} else "TEXT"
    with localcontext() as context:
        context.prec = 128
        value = Decimal(match["n"]) * {None: 1, "万": 10000, "亿": 100000000}[match["u"]]
    profile = decimal_profile(value, precision=38, scale=10)
    if not profile["fits"]:
        return None, token, False, "EXACT_VALUE_TEXT"
    return value, None, False, "ZERO" if value == 0 else "EXACT_DECIMAL"


def summary_observation(
    raw: dict[str, Any], *, source_sha256: str, revision: str,
    resolver: FinancialCompanyResolver, observed_at: datetime, contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) or not revision
            or contract.get("contract_id") != CONTRACT_ID):
        raise BoundedError("FINANCIAL_SUMMARY_SOURCE_BINDING_INVALID")
    proof = financial_literal_identity(raw, observed_at)
    observed = _clock(observed_at)
    held: list[dict[str, Any]] = []
    selected: dict[tuple[date, str], tuple[str, str, str, str]] = {}
    conflicts: set[tuple[date, str]] = set()
    counts: Counter[str] = Counter()
    for number, lines in enumerate(_frames(raw["content"])):
        frame = {"frame_index": number, "original_block_sha256": digest("\n".join(lines))}
        try:
            days, metrics = _matrix(lines)
        except (BoundedError, ValueError) as exc:
            held.append(dict(frame, reason=str(exc)))
            continue
        for label, values in metrics:
            rule = contract["financial_metrics"].get(label)
            if rule is None:
                counts["unsupported_cells"] += len(values)
                continue
            for day, token in zip(days, values, strict=True):
                if day > THROUGH:
                    counts["excluded_after_cutoff_cells"] += 1
                    continue
                key = day, rule[0]
                item = label, token, str(rule[1]), str(frame["original_block_sha256"])
                if key in selected and selected[key][:3] != item[:3]:
                    conflicts.add(key)
                elif key not in selected:
                    selected[key] = item
    reports: dict[date, dict[str, Any]] = {}
    concepts, facts = [], []
    common = {"record_source": raw["source"], "source_revision": revision,
              "source_artifact_sha256": source_sha256, "knowledge_from": observed,
              "knowledge_to": None}
    for (day, field), (label, token, unit, block_sha) in selected.items():
        if (day, field) in conflicts:
            continue
        parent, reason = resolver.resolve_for_report(raw["stock_code"], period_end=day)
        if parent is None:
            counts["held_parent_cells:" + str(reason)] += 1
            continue
        if parent.knowledge_from > observed:
            raise BoundedError("FINANCIAL_SUMMARY_PARENT_NOT_AVAILABLE")
        source_id = f"stock_code={raw['stock_code']}&category=财务分析&period_end={day.isoformat()}"
        report_id = _stable_id(
            "financial_report", NAMESPACE, parent.company_id, raw["stock_code"], day.isoformat(),
        )
        if day not in reports:
            reports[day] = {**common, "source_record_id": source_id,
                           "period_start": None, "period_end": day, "published_at": None,
                           "financial_report_id": report_id, "company_id": parent.company_id,
                           "report_type": "SOURCE_LITERAL_FINANCIAL_SUMMARY",
                           "statement_scope": "F10_MAJOR_INDICATORS_LITERAL",
                           "accounting_standard": "PROVIDER_NATIVE_UNSPECIFIED",
                           "audit_status": None, "restatement_flag": False}
        concept_id = _stable_id("financial_concept", NAMESPACE, source_sha256, field, unit)
        concepts.append({**common, "source_record_id": "definition:" + label,
                         "effective_from": _period_coordinate(day), "effective_to": None,
                         "financial_concept_id": concept_id, "taxonomy_namespace": NAMESPACE,
                         "taxonomy_version": CONTRACT_ID, "concept_code": field,
                         "concept_name": label,
                         "statement_type": "SOURCE_LITERAL_FINANCIAL_SUMMARY",
                         "data_type": "source_literal_scalar",
                         "period_type": "provider_native_mixed",
                         "balance_type": None})
        value, text, nil, classification = _scalar(token, unit)
        counts["scalar:" + classification] += 1
        dimensions = json.dumps({"source_literal_label": label, "source_literal_token": token,
                                 "source_block_sha256": block_sha,
                                 "source_content_sha256": raw["content_sha256"],
                                 "unit_authority": "EXPLICIT_LITERAL_COLUMN_LABEL",
                                 "currency": "UNAVAILABLE_NOT_INFERRED"},
                                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        context_hash = hashlib.sha256(dimensions.encode()).hexdigest()
        facts.append({**common, "source_record_id": source_id + "&concept=" + field,
                      "period_start": None, "period_end": day, "published_at": None,
                      "financial_fact_id": _stable_id("financial_fact", report_id, concept_id),
                      "financial_report_id": report_id, "financial_concept_id": concept_id,
                      "unit_code": unit, "value_decimal": value, "value_text": text,
                      "dimensions_json": dimensions, "context_hash": context_hash,
                      "decimals": None, "is_nil": nil})
    rows = dict(zip(TABLES, [list(reports.values()), financial_concept_versions(concepts), facts],
                    strict=True))
    for table, schema in financial_reporting_schemas().items():
        typed = pa.Table.from_pylist(rows[table], schema=schema)
        if typed.to_pylist() != rows[table] or any(
            not f.nullable and typed.column(f.name).null_count for f in schema
        ):
            raise BoundedError("FINANCIAL_SUMMARY_SERIALIZED_ATOM_CHANGED")
    proof.update(contract_id=CONTRACT_ID, source_member_sha256=source_sha256,
                 source_revision=revision, held_frames=held,
                 conflicting_coordinates=len(conflicts),
                 unsupported_cells=counts["unsupported_cells"],
                 excluded_after_cutoff_cells=counts["excluded_after_cutoff_cells"],
                 classification_counts=dict(counts), currency_CNY_inferred=False,
                 provider_PIT_qualified=False, publication_inferred=False,
                 row_counts={t: len(rows[t]) for t in TABLES},
                 typed_rows_sha256={t: digest(rows[t]) for t in TABLES})
    return rows, proof
