"""New official observations when a retained document reference is unavailable.

The incoming reference is a new job-local observation of an actual official
index row. It never claims recovery or acceptance of the different old ID.
Listing, venue, calendar, exact terms and source clocks retain the existing
distribution gates. No old row or knowledge interval is replaced.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base231_dated_distribution import replay_terms_domain
from .base231_disclosure_body import project_body
from .disclosure_event import disclosure_event_schemas
from .financial_reporting import (
    FinancialCompanyIdentity,
    financial_company_resolver_from_listed_company_rows,
)

CONTRACT_ID = "p10-base231-new-current-official-action-reference-v1"


def verify_current_receipts(
    index_receipt: dict[str, Any],
    transport: dict[str, Any],
    normalized: dict[str, Any],
    *,
    index_sha256: str,
    document_sha256: str,
    document_bytes: int,
    transport_receipt_sha256: str,
    document_uri: str,
) -> None:
    """The legacy success-status field cannot replace actual transport evidence."""
    if (
        index_receipt.get("URI") != "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        or type(index_receipt.get("http_status")) is not int
        or index_receipt["http_status"] != 200
        or index_receipt.get("error") is not None
        or index_receipt.get("response_cap_reached") is not False
        or index_receipt.get("body_sha256") != index_sha256
        or type(transport.get("status")) is not int
        or transport["status"] != 200
        or transport.get("error") is not None
        or transport.get("limit_reached") is not False
        or transport.get("URI") != document_uri
        or "sha256:" + str(transport.get("body_sha256")) != document_sha256
        or transport.get("body_bytes") != document_bytes
        or normalized.get("completed_at") != transport.get("UTC")
        or normalized.get("raw_transport_receipt_sha256") != transport_receipt_sha256
        or normalized.get("actual_transport") != "urllib.request"
        or normalized.get("curl_was_executed") is not False
    ):
        raise BoundedError("CURRENT_OFFICIAL_SOURCE_RECEIPT_CHANGED")


def current_reference(
    actual: dict[str, Any],
    *,
    parent: FinancialCompanyIdentity,
    index_sha256: str,
    index_received_at: datetime,
    observed_at: datetime,
) -> dict[str, Any]:
    """Create a typed index observation and an explicit current-only bridge."""
    import pyarrow as pa  # type: ignore[import-untyped]

    clock = instant(observed_at)
    milliseconds = actual.get("announcementTime")
    if (
        type(milliseconds) is not int
        or not isinstance(actual.get("announcementId"), str)
        or not re.fullmatch(r"[0-9]+", str(actual.get("announcementId", "")))
        or actual.get("secCode") != parent.stock_code
        or not actual.get("announcementTitle")
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", index_sha256)
    ):
        raise BoundedError("CURRENT_OFFICIAL_REFERENCE_IDENTITY_UNPROVEN")
    published = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)
    business_date = published.astimezone(ZoneInfo("Asia/Shanghai")).date()
    effective = datetime.combine(business_date, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    received = instant(index_received_at)
    expected = "finalpage/" + business_date.isoformat() + "/" + actual["announcementId"] + ".PDF"
    if (
        actual.get("adjunctUrl") != expected
        or published > clock
        or published > received
        or received > clock
        or business_date > date(2026, 9, 30)
        or parent.knowledge_from > clock
        or parent.effective_from > instant(effective)
        or parent.effective_to is not None
        and instant(effective) >= parent.effective_to
    ):
        raise BoundedError("CURRENT_OFFICIAL_REFERENCE_DATE_OR_URI_UNPROVEN")
    native = dict(
        record_source="cninfo.new_current_official_index",
        source_record_id=actual["announcementId"],
        source_revision=CONTRACT_ID,
        source_artifact_sha256=index_sha256,
        knowledge_from=clock,
        knowledge_to=None,
        effective_at=published,
        announced_at=published,
        disclosure_id="disclosure_"
        + digest([CONTRACT_ID, actual["announcementId"], parent.company_id])[7:39],
        company_id=parent.company_id,
        disclosure_type="CURRENT_OFFICIAL_INDEX_OBSERVATION",
        source_document_id=actual["announcementId"],
        title=actual["announcementTitle"],
        document_uri="https://static.cninfo.com.cn/" + expected,
        language="zh-CN",
    )
    if pa.Table.from_pylist(
        [native], schema=disclosure_event_schemas()["dwd_disclosure"]
    ).to_pylist() != [native]:
        raise BoundedError("CURRENT_OFFICIAL_REFERENCE_NATIVE_CHANGED")
    return dict(
        **native,
        business_date=business_date.isoformat(),
        explicit_references=[dict(security_id=parent.security_id)],
        source_row_sha256=digest(native),
        source_observed_at=received,
        reference_origin=CONTRACT_ID,
        old_reference_recovered=False,
        old_base_acceptance_claimed=False,
        historical_PIT_restored=False,
    )


def replay_current_action(root: Path) -> dict[str, Any]:
    """Replay every native term and current disclosure with no old source read."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    plan = json.loads((root / "INPUTS.json").read_bytes())
    code_path = Path("src/quanthus/pipelines/securities_warehouse/base231_current_action.py")
    if (
        plan.get("current_reference_contract") != CONTRACT_ID
        or not plan.get("selection")
        or plan.get("code_inputs", {}).get(str(code_path)) != file_sha(code_path)
    ):
        raise BoundedError("CURRENT_OFFICIAL_REFERENCE_CONTRACT_REQUIRED")
    # Includes complete physical-member hashes, parent snapshots, clocks,
    # listing/calendar closure, exact decimals and re-extracted PDF terms.
    result = replay_terms_domain(root)
    parents = {
        table: [
            row
            for member in plan["parents"][table]
            for row in pq.ParquetFile(root / member["local"]).read().to_pylist()
        ]
        for table in ["dwd_security", "dwd_security_identifier"]
    }
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    output: dict[str, list[dict[str, Any]]] = {
        t: [] for t in ["dwd_disclosure", "dwd_disclosure_subject"]
    }
    now = datetime.fromisoformat(plan["observed_at"])
    for spec in plan["selection"]:
        selected = json.loads((root / spec["selection"]).read_bytes())["selection"]
        actual = selected["actual_index_row"]
        bridge = selected["original_disclosure"]
        parent, _ = resolver.resolve_for_report(
            actual["secCode"], period_end=date.fromisoformat(bridge["business_date"])
        )
        index_receipt = json.loads((root / spec["index_receipt"]).read_bytes())
        receipt = json.loads((root / spec["receipt"]).read_bytes())
        transport_path = root / spec["transport_receipt"]
        verify_current_receipts(
            index_receipt,
            json.loads(transport_path.read_bytes()),
            receipt,
            index_sha256=file_sha(root / spec["index"]),
            document_sha256=file_sha(root / spec["PDF"]),
            document_bytes=(root / spec["PDF"]).stat().st_size,
            transport_receipt_sha256=file_sha(transport_path),
            document_uri=selected["actual_document_URI"],
        )
        if parent is None or canonical(bridge) != canonical(
            current_reference(
                actual,
                parent=parent,
                index_sha256=file_sha(root / spec["index"]),
                index_received_at=datetime.fromisoformat(index_receipt["completed_at"]),
                observed_at=now,
            )
        ):
            raise BoundedError("CURRENT_OFFICIAL_REFERENCE_NEW_OBSERVATION_CHANGED")
        body = subprocess.run(
            ["pdftotext", "-layout", str(root / spec["PDF"]), "-"],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout.decode()
        rows, _ = project_body(
            selected,
            receipt,
            index=json.loads((root / spec["index"]).read_bytes()),
            body_sha256=file_sha(root / spec["PDF"]),
            body_text=body,
            parent=parent,
            observed_at=now,
        )
        for table, values in rows.items():
            output[table].extend(values)
    for table, values in output.items():
        physical = pq.ParquetFile(root / "outputs" / (table + ".parquet")).read()
        if physical.schema != disclosure_event_schemas()[table] or physical.to_pylist() != values:
            raise BoundedError("CURRENT_OFFICIAL_REFERENCE_DISCLOSURE_REPLAY_CHANGED")
    if digest(output) != plan.get("current_disclosure_semantic_sha256"):
        raise BoundedError("CURRENT_OFFICIAL_REFERENCE_DISCLOSURE_SEMANTIC_CHANGED")
    return {
        **result,
        "status": "PASS_NEW_CURRENT_OFFICIAL_ACTION_AND_DISCLOSURE_REPLAY",
        "rows": {**result["rows"], **{t: len(v) for t, v in output.items()}},
        "old_reference_recovered": False,
        "old_base_acceptance_claimed": False,
    }
