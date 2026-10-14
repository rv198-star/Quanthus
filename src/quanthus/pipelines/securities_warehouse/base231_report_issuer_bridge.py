"""One current-code report observation bound to its unchanged dated issuer.

This never creates a security identifier, listing interval or transaction ID.
The proposal is evidence of explicit legal continuity, not code activation.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from .base22 import digest, file_sha
from .base22_bounded import BoundedError
from .base22_f10_ownership import _cells
from .base22_holder_literals import _CHANGE_HEADERS
from .base22_outputs import instant
from .base22_sources import _tree, physical_path
from .base231_holder_scope import TABLE, project_change_tables
from .financial_reporting import FinancialCompanyIdentity, FinancialCompanyResolver
from .ownership_capital import ownership_capital_schemas

CONTRACT_ID = "p10-base231-one-current-report-issuer-continuity-observation-v1"
ORIGINAL_ROW = "sha256:eb593877a6c8a089d27cf6721e609eae853e228920be14c2ece16ea183d6ad83"
ORIGINAL_NOTICE = "sha256:808b825b3831707c1246826d03ce80cd1565ffdd57c2f3f61d2d1f38a7482d18"
ORIGINAL_MEMBER = "sha256:9744415f523c0771e22e65937e70817f789341dd6137fd76b047bd5e2f8e9fef"
NOTICE_URI = "https://static.cninfo.com.cn/finalpage/2025-01-23/1222419352.PDF"


def project_report_observation(
    raw: dict[str, Any],
    *,
    raw_sha256: str,
    source_sha256: str,
    dated_parent: FinancialCompanyIdentity,
    current_parent: FinancialCompanyIdentity,
    official_text: str,
    official_receipt: dict[str, Any],
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = re.sub(r"\s+", "", official_text)
    clock = instant(observed_at)
    day = date(2025, 1, 17)
    from .disclosure_event import _effective_start

    at = _effective_start(day)
    if (
        digest(raw) != raw_sha256
        or source_sha256 != ORIGINAL_MEMBER
        or raw.get("stock_code") != "302132"
        or dated_parent.stock_code != "300114"
        or current_parent.stock_code != "302132"
        or dated_parent.company_id != current_parent.company_id
        or dated_parent.security_id != current_parent.security_id
        or dated_parent.effective_from > at
        or dated_parent.effective_to is not None
        and at >= dated_parent.effective_to
        or current_parent.effective_from > _effective_start(date(2026, 9, 30))
        or current_parent.effective_to is not None
        and _effective_start(date(2026, 9, 30)) >= current_parent.effective_to
        or max(dated_parent.knowledge_from, current_parent.knowledge_from) > clock
        or official_receipt.get("body_sha256") != ORIGINAL_NOTICE
        or official_receipt.get("URI") != NOTICE_URI
        or official_receipt.get("curl_exit") != 0
        or official_receipt.get("http_status") != "200"
        or instant(datetime.fromisoformat(official_receipt["actual_captured_at"])) > clock
        or any(
            token not in text
            for token in [
                "300114",
                "302132",
                "公司法人主体存续",
                "上市主体没有发生实质变化",
                "权利义务平移",
            ]
        )
    ):
        raise BoundedError("REPORT_ISSUER_EXACT_DATED_CONTINUITY_UNPROVEN")

    class SelectedReportResolver:
        def resolve_for_report(
            self, stock_code: str, *, period_end: date
        ) -> tuple[FinancialCompanyIdentity | None, str | None]:
            if stock_code == "302132" and period_end == day:
                return dated_parent, None
            return None, "REPORT_ISSUER_BRIDGE_OUTSIDE_ONE_SELECTED_COORDINATE"

    # Reuse the independent-row interpretation boundary: original unit/header,
    # exact original wrapped row bytes and actual closing border. Unrelated
    # source rows cannot acquire the one-coordinate identity authorization.
    lines = raw["content"].splitlines()
    starts = [n for n, line in enumerate(lines) if tuple(_cells(line)) in _CHANGE_HEADERS]
    views = []
    for block, start in enumerate(starts):
        stop = starts[block + 1] if block + 1 < len(starts) else len(lines)
        close = next((n for n in range(start + 1, stop) if lines[n].strip().startswith("└")), None)
        if close is None:
            continue
        row_starts = [
            n
            for n in range(start + 1, close)
            if len(_cells(lines[n])) == 7
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", _cells(lines[n])[0])
        ]
        for i, first in enumerate(row_starts):
            if _cells(lines[first])[0] != day.isoformat():
                continue
            end = row_starts[i + 1] if i + 1 < len(row_starts) else close
            view = "\n".join(lines[start : row_starts[0]] + lines[first:end] + [lines[close]])
            views.append((view, digest(lines[first:end])))
    if len(views) != 1:
        raise BoundedError("REPORT_ISSUER_EXACT_SINGLE_ORIGINAL_ROW_REQUIRED")
    view, atom_sha = views[0]
    derived = dict(
        raw, content=view, content_sha256="sha256:" + hashlib.sha256(view.encode()).hexdigest()
    )
    outputs, source_proof = project_change_tables(
        derived,
        source_sha256=source_sha256,
        resolver=cast(FinancialCompanyResolver, SelectedReportResolver()),
        observed_at=observed_at,
    )
    rows = outputs[TABLE]
    if len(rows) != 1:
        raise BoundedError("REPORT_ISSUER_EXACT_SINGLE_SOURCE_OBSERVATION_UNPROVEN")
    row = rows[0]
    row.update(
        record_source="p10_current_report_issuer_continuity_observation",
        source_revision=CONTRACT_ID,
        source_record_id=digest([raw_sha256, source_proof["typed_rows_sha256"]]),
        shareholding_change_id="shareholding_change_" + digest([CONTRACT_ID, raw_sha256])[7:39],
    )
    return rows, dict(
        contract_id=CONTRACT_ID,
        raw_row_sha256=raw_sha256,
        source_member_sha256=source_sha256,
        original_atom_sha256=atom_sha,
        derived_view_sha256=derived["content_sha256"],
        derived_view_is_original_vendor_payload=False,
        dated_parent_sha256=digest(asdict(dated_parent)),
        current_parent_sha256=digest(asdict(current_parent)),
        official_PDF_sha256=ORIGINAL_NOTICE,
        source_proof=source_proof,
        rows_sha256=digest(rows),
        selected_report_business_date=day,
        original_trading_code_activated=False,
        new_security_identifier_rows=0,
        original_source_stock_code_unchanged=True,
        transaction_equivalence_granted=False,
        historical_PIT_qualified=False,
        old_values_overwritten=False,
    )


def replay_report_bridge(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .financial_reporting import financial_company_resolver_from_listed_company_rows

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
        or plan.get("complete_Base") is not False
        or plan.get("through_date") != "2026-09-30"
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("market_alias_created") is not False
        or plan.get("transaction_equivalence_granted") is not False
        or plan.get("original_member_sha256") != ORIGINAL_MEMBER
    ):
        raise BoundedError("REPORT_ISSUER_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("REPORT_ISSUER_PHYSICAL_SET_CHANGED")
    for n, m in plan["members"].items():
        p = physical_path(root / n)
        if (
            root.resolve() not in p.parents
            or p.stat().st_nlink != 1
            or p.stat().st_size != m["bytes"]
            or file_sha(p) != m["sha256"]
        ):
            raise BoundedError("REPORT_ISSUER_MEMBER_CHANGED")
    for n, sha in plan["code_inputs"].items():
        if file_sha(Path(n)) != sha:
            raise BoundedError("REPORT_ISSUER_ORDINARY_CODE_CHANGED")
    manifest = root / "inputs/image-manifest.json"
    if (
        file_sha(manifest)
        != "sha256:91845422851368028e4e8a6b83e0dc9ba2a9e30dfdd8e4d6de892f64adac1d7f"
    ):
        raise BoundedError("REPORT_ISSUER_RETAINED_PARENT_CHANGED")
    if (
        file_sha(root / "inputs/image-materialization.json")
        != json.loads(manifest.read_bytes())["materialization_sha256"]
    ):
        raise BoundedError("REPORT_ISSUER_NATIVE_LAYOUT_CHANGED")
    layout = json.loads((root / "inputs/image-materialization.json").read_bytes())["layout"]
    parents: dict[str, list[dict[str, Any]]] = {}
    for t, members in plan["parents"].items():
        originals = {m["path"]: m["sha256"] for m in layout[t]["files"]}
        if {m["original"] for m in members} != set(originals):
            raise BoundedError("REPORT_ISSUER_PARENT_SET_CHANGED")
        parents[t] = []
        for m in members:
            if file_sha(root / m["local"]) != originals[m["original"]]:
                raise BoundedError("REPORT_ISSUER_PARENT_FILE_CHANGED")
            parents[t].extend(pq.ParquetFile(root / m["local"]).read().to_pylist())
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    old, _ = resolver.resolve_for_report("300114", period_end=date(2025, 1, 17))
    current, _ = resolver.resolve_for_report("302132", period_end=date(2026, 9, 30))
    if old is None or current is None:
        raise BoundedError("REPORT_ISSUER_DATED_NATIVE_PARENT_UNAVAILABLE")
    evidence = json.loads((root / "inputs/frozen-original-selection.json").read_bytes())
    raw = json.loads((root / "inputs/original-source-row.json").read_bytes())
    selection = next(r for r in evidence["selected"] if r["code"] == "302132")
    if (
        selection["raw_row_sha256"] != ORIGINAL_ROW
        or digest(raw) != ORIGINAL_ROW
        or selection["member_sha256"] != plan["original_member_sha256"]
    ):
        raise BoundedError("REPORT_ISSUER_FROZEN_ORIGINAL_ROW_CHANGED")
    if file_sha(root / "inputs/official.pdf") != ORIGINAL_NOTICE:
        raise BoundedError("REPORT_ISSUER_ORIGINAL_NOTICE_CHANGED")
    body = subprocess.run(
        ["pdftotext", "-layout", str(root / "inputs/official.pdf"), "-"],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout.decode()
    rows, proof = project_report_observation(
        raw,
        raw_sha256=ORIGINAL_ROW,
        source_sha256=plan["original_member_sha256"],
        dated_parent=old,
        current_parent=current,
        official_text=body,
        official_receipt=json.loads((root / "inputs/official-receipt.json").read_bytes()),
        observed_at=datetime.fromisoformat(plan["observed_at"]),
    )
    actual = pq.ParquetFile(root / "outputs/dwd_shareholding_change.parquet").read()
    if (
        actual.schema != ownership_capital_schemas()[TABLE]
        or actual.to_pylist() != rows
        or digest(rows) != plan["semantic_sha256"]
        or digest(proof) != plan["proof_sha256"]
    ):
        raise BoundedError("REPORT_ISSUER_NATIVE_RECONSTRUCTION_CHANGED")
    return dict(
        status="PASS_FULL_LOCAL_ONE_REPORT_ISSUER_BRIDGE_REPLAY",
        rows=len(rows),
        input_identity=plan["identity"],
        complete_Base=False,
        original_trading_code_activated=False,
        transaction_equivalence_granted=False,
        old_source_roots_or_network_required=False,
    )
