"""September-patch frame certificates and source-reported holder change atoms.

Complete literal tables qualify only their observed rows. Missing ranks are not
exits; equal numerical observations do not identify transactions. Frozen August
parsers/evaluator identities remain byte-identical.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_financial import _clock
from .base22_holder_change_source import _quantity, _ratio, _share_header
from .base22_holder_literals import _CHANGE_HEADERS
from .base23_ownership_reconfirmation import literal_identity
from .financial_reporting import FinancialCompanyResolver
from .ownership_capital import (
    _effective_start,
    _stable_holder_key,
    _stable_id,
    ownership_capital_schemas,
)

CHANGE_CONTRACT_ID = "p10-base231-source-reported-holder-changes-v1"
THROUGH = date(2026, 9, 30)
TABLE = "dwd_shareholding_change"


def certify_frames(proof: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if proof.get("typed_rows") != len(rows) or proof.get("typed_rows_sha256") != digest(rows):
        raise BoundedError("HOLDER_FRAME_CERTIFICATE_ROW_DIGEST_CHANGED")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        day = row["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        grouped.setdefault((day, row["holding_type"]), []).append(row)
    accepted: dict[tuple[str, str], dict[str, Any]] = {}
    held: list[dict[str, Any]] = []
    for frame in proof["frames"]:
        if frame["status"] not in {
            "QUALIFIED_COMPLETE_LITERAL_MATRIX", "IDENTICAL_COMPLETE_FRAME_RECONFIRMED",
        }:
            held.append(frame)
            continue
        key = frame["business_date"], frame["holding_type"]
        if key not in grouped or frame["typed_rows_sha256"] != digest(grouped[key]):
            raise BoundedError("HOLDER_FRAME_CERTIFICATE_SOURCE_EXTENT_CHANGED")
        accepted.setdefault(key, frame)
    if set(accepted) != set(grouped):
        raise BoundedError("HOLDER_FRAME_CERTIFICATE_UNDECLARED_ROWS")
    return {"source_member_sha256": proof["source_member_sha256"],
            "literal_identity": proof["literal_identity"], "rows": len(rows),
            "typed_rows_sha256": digest(rows), "certified_frames": len(accepted),
            "held_frames": len(held), "frames": list(accepted.values()), "held": held,
            "issuer_top_ten_coverage_inferred": False, "absent_holder_is_exit": False,
            "provider_PIT_qualified": False}


def event_equivalence(left: dict[str, Any], right: dict[str, Any]) -> str:
    """Classify original qualified IDs, never holder/day/amount similarities.

    The caller must bind fields to retained original authority. A generated
    source-row hash or a legacy row_key is not an original transaction identity.
    """
    fields = ("original_provider", "original_event_id", "original_disclosure_id")
    if not all(isinstance(left.get(k), str) and left[k] and left[k] == right.get(k)
               for k in fields):
        return "UNPROVEN_OVERLAP_RETAIN_SEPARATE"
    if not all(re.fullmatch(r"sha256:[0-9a-f]{64}", str(x.get("original_atom_sha256")))
               for x in (left, right)):
        raise BoundedError("HOLDER_CHANGE_ORIGINAL_ATOM_HASH_REQUIRED")
    if left["original_atom_sha256"] == right["original_atom_sha256"]:
        return "EXACT_ORIGINAL_EVENT_RECONFIRMATION"
    return "ORIGINAL_EVENT_BODY_REVISION_RETAIN_NEW_OBSERVATION"


def project_change_tables(
    raw: dict[str, Any], *, source_sha256: str, resolver: FinancialCompanyResolver,
    through_date: date = THROUGH, observed_at: datetime | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    if through_date != THROUGH or observed_at is None:
        raise BoundedError("HOLDER_CHANGE_SUCCESSOR_EXPLICIT_SCOPE_AND_OBSERVATION_REQUIRED")
    identity = literal_identity(raw)
    observed, acquired = _clock(observed_at), identity["actual_acquisition_instant"]
    if (raw["category"] != "股东研究" or acquired > observed
            or identity["wrapper_market_date"] > through_date
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256)):
        raise BoundedError("HOLDER_CHANGE_SUCCESSOR_SOURCE_OR_CLOCK_UNQUALIFIED")
    lines = raw["content"].splitlines()
    starts = [n for n, line in enumerate(lines) if tuple(_cells(line)) in _CHANGE_HEADERS]
    if len(starts) > 16:
        raise BoundedError("HOLDER_CHANGE_SUCCESSOR_TABLE_BOUND")
    candidates: dict[str, list[tuple[dict[str, Any], str]]] = {}
    blocks: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for index, start in enumerate(starts):
        bound = starts[index + 1] if index + 1 < len(starts) else len(lines)
        end = next((n + 1 for n in range(start + 1, bound)
                    if lines[n].strip().startswith("└")), bound)
        frame = {"original_block_sha256": digest("\n".join(lines[start:end])),
                 "block_index": index}
        try:
            if not lines[end - 1].strip().startswith("└"):
                raise BoundedError("HOLDER_CHANGE_SUCCESSOR_FRAME_INCOMPLETE")
            header = _cells(lines[start])
            pending: list[list[str]] = []
            units = None
            for line in lines[start + 1:end - 1]:
                cells = _cells(line)
                if not cells or not any(cells):
                    if line.strip() and not line.strip().startswith(("├", "┌", "│")):
                        raise BoundedError("HOLDER_CHANGE_SUCCESSOR_NONEMPTY_ROW_UNACCOUNTED")
                    continue
                if len(cells) != 7:
                    raise BoundedError("HOLDER_CHANGE_SUCCESSOR_NONEMPTY_ROW_UNACCOUNTED")
                if not cells[0] and not cells[1] and any("股" in c or "%" in c
                                                       for c in cells[2:6]):
                    if units is not None or pending:
                        raise BoundedError("HOLDER_CHANGE_SUCCESSOR_UNIT_HEADER_CONFLICT")
                    units = cells
                elif pending and not cells[0] and not any(cells[2:6]) and (cells[1] or cells[6]):
                    pending[-1][1] += cells[1]
                    pending[-1][6] += cells[6]
                elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
                    pending.append(cells)
                else:
                    raise BoundedError("HOLDER_CHANGE_SUCCESSOR_DATE_OR_ROW_UNACCOUNTED")
            if len(pending) > 2048:
                raise BoundedError("HOLDER_CHANGE_SUCCESSOR_ROW_BOUND")
            block_rows = []
            for cells in pending:
                day = date.fromisoformat(cells[0])
                if day > through_date:
                    counts["excluded_after_cutoff_rows"] += 1
                    continue
                if day > acquired.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    raise BoundedError("HOLDER_CHANGE_SUCCESSOR_FUTURE_IMPLEMENTATION_UNPROVEN")
                if (not cells[1] or not cells[6] or cells[6] != "员工持股计划"
                        and re.search(r"拟|计划|预计|意向", cells[6])):
                    raise BoundedError("HOLDER_CHANGE_SUCCESSOR_ACTUAL_EVENT_UNPROVEN")
                parent, reason = resolver.resolve_for_report(raw["stock_code"], period_end=day)
                if parent is None:
                    raise BoundedError(str(reason))
                if parent.knowledge_from > observed:
                    raise BoundedError("HOLDER_CHANGE_SUCCESSOR_PARENT_NOT_AVAILABLE")
                before = _quantity(cells[2], explicit_share_unit=bool(
                    units and _share_header(units[2])), signed=False)
                changed = _quantity(cells[3], explicit_share_unit=bool(
                    units and _share_header(units[3])), signed=True)
                after = _quantity(cells[4], explicit_share_unit=bool(
                    units and _share_header(units[4])), signed=False)
                ratio = _ratio(cells[5], explicit_percent_unit=bool(units and "%" in units[5]),
                               delta=header[5] == "变动比例")
                key = digest([parent.company_id, parent.security_id,
                              _stable_holder_key(cells[1]), day, cells[6]])
                atom = digest([cells, header, units, before, changed, after, ratio])
                row = dict(record_source="p10_retained_f10_holder_change_table",
                           source_record_id=digest([raw["stock_code"], atom]),
                           source_revision=CHANGE_CONTRACT_ID,
                           source_artifact_sha256=source_sha256, knowledge_from=observed,
                           knowledge_to=None, effective_at=_effective_start(day), announced_at=None,
                           shareholding_change_id=_stable_id("shareholding_change",
                                                             CHANGE_CONTRACT_ID, key),
                           company_id=parent.company_id, security_id=parent.security_id,
                           shareholder_source_name=cells[1], shareholder_id=None,
                           change_type=cells[6], share_change=changed,
                           percentage_change=ratio if header[5] == "变动比例" else None,
                           post_holding=after,
                           post_percentage=ratio if header[5] == "变动后占" else None,
                           disclosure_id=None, holder_match_key=_stable_holder_key(cells[1]),
                           raw_share_change=cells[3],
                           share_change_unit="SHARE" if changed is not None else None,
                           raw_percentage_change=cells[5] if header[5] == "变动比例" else None,
                           percentage_change_unit="SOURCE_DECLARED_PERCENT_CHANGE_BASIS_UNAVAILABLE"
                           if ratio is not None and header[5] == "变动比例" else None,
                           raw_post_holding=cells[4],
                           post_holding_unit="SHARE" if after is not None else None,
                           raw_post_percentage=cells[5] if header[5] == "变动后占" else None,
                           post_percentage_unit=("PERCENT_OF_SOURCE_TOTAL_SHARES"
                                                 if units and "总股本" in units[5]
                                                 else
                                                 "SOURCE_DECLARED_POST_PERCENT_BASIS_UNAVAILABLE")
                           if ratio is not None and header[5] == "变动后占" else None,
                           parser_version=CHANGE_CONTRACT_ID)
                block_rows.append((key, row, atom))
            for key, row, atom in block_rows:
                candidates.setdefault(key, []).append((row, atom))
            frame.update(status="COMPLETE_SOURCE_REPORTED_CHANGE_TABLE",
                         source_data_rows=len(pending), source_atoms_sha256=digest(block_rows))
        except (BoundedError, ValueError) as exc:
            frame.update(status="HELD_NOT_EMPTY_COVERAGE", reason=str(exc))
        blocks.append(frame)
    sections = [n for n, line in enumerate(lines)
                if re.fullmatch(r"【\d+\.股东持股变动】", line.strip())]
    for start in sections:
        end = next((n for n in range(start + 1, len(lines))
                    if re.match(r"【\d+\.", lines[n].strip())), len(lines))
        if any(start < n < end for n in starts):
            continue
        body = [line.strip() for line in lines[start + 1:end] if line.strip()]
        if body == ["暂无数据"]:
            counts["source_declared_empty_sections"] += 1
        else:
            blocks.append({"status": "HELD_NOT_EMPTY_COVERAGE",
                           "reason": "HOLDER_CHANGE_NONEMPTY_SECTION_HEADER_UNQUALIFIED",
                           "original_block_sha256": digest("\n".join(lines[start:end]))})
    output = [values[0][0] for _, values in sorted(candidates.items())
              if len({atom for _, atom in values}) == 1]
    counts["held_natural_keys"] = sum(len({a for _, a in v}) > 1 for v in candidates.values())
    schema = ownership_capital_schemas()[TABLE]
    typed = pa.Table.from_pylist(output, schema=schema)
    if typed.to_pylist() != output:
        raise BoundedError("HOLDER_CHANGE_SUCCESSOR_SERIALIZED_ATOM_CHANGED")
    proof = {"contract_id": CHANGE_CONTRACT_ID, "source_member_sha256": source_sha256,
             "literal_identity": identity, "observed_at": observed, "business_through": THROUGH,
             "blocks": blocks, "typed_rows": len(output), "typed_rows_sha256": digest(output),
             "counts": dict(counts), "transaction_equivalence_granted": False,
             "provider_PIT_qualified": False, "consumer_admission": "NOT_GRANTED"}
    return {TABLE: output}, proof
