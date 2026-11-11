"""Actual official bodies bound to retained late-September disclosure identities.

This is a source-body qualification, not an interpretation of every announced
action, accounting value or holding change. Existing observations remain intact.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest, file_sha, load_contract
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_sources import _tree, physical_path
from .disclosure_event import disclosure_event_schemas
from .financial_reporting import financial_company_resolver_from_listed_company_rows

CONTRACT_ID = "p10-base231-exact-official-disclosure-body-observation-v1"


def project_body(
    selection: dict[str, Any],
    receipt: dict[str, Any],
    *,
    index: dict[str, Any],
    body_sha256: str,
    body_text: str,
    parent: Any,
    observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    old, actual = selection["original_disclosure"], selection["actual_index_row"]
    clock = instant(observed_at)
    received = instant(datetime.fromisoformat(receipt["completed_at"]))
    milliseconds = actual.get("announcementTime")
    if type(milliseconds) is not int:
        raise BoundedError("DISCLOSURE_BODY_PUBLICATION_CLOCK_REQUIRED")
    published = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)
    business_date = published.astimezone(ZoneInfo("Asia/Shanghai")).date()
    effective = datetime.combine(business_date, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    uri = "https://static.cninfo.com.cn/" + str(actual.get("adjunctUrl"))
    codes = set(
        re.findall(r"(?:证券|股票|A股)代码[:：]?([0-9]{6})", re.sub(r"\s+", "", body_text[:4000]))
    )
    if (
        actual not in (index.get("announcements") or [])
        or str(actual.get("announcementId")) != old["source_document_id"]
        or actual.get("secCode") != parent.stock_code
        or old["company_id"] != parent.company_id
        or {r["security_id"] for r in old["explicit_references"] if r.get("security_id")}
        != {parent.security_id}
        or not re.fullmatch(
            r"finalpage/\d{4}-\d{2}-\d{2}/[0-9]+\.[Pp][Dd][Ff]", str(actual.get("adjunctUrl"))
        )
        or selection["actual_document_URI"] != uri
        or receipt["URI"] != uri
        or receipt["curl_exit"] != 0
        or receipt["http_status"] != "200"
        or receipt["body_sha256"] != body_sha256
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", body_sha256)
        or not body_text.strip()
        or len(body_text.encode()) > 16 * 1024**2
        or codes
        and parent.stock_code not in codes
        or not actual.get("announcementTitle")
        or date.fromisoformat(old["business_date"]) != business_date
        or business_date > date(2026, 9, 30)
        or published > received
        or received > clock
        or parent.knowledge_from > clock
        or parent.effective_from > instant(effective)
        or parent.effective_to is not None
        and instant(effective) >= parent.effective_to
    ):
        raise BoundedError("DISCLOSURE_BODY_EXACT_SOURCE_PARENT_DATE_UNPROVEN")
    common = dict(
        record_source="cninfo.exact_original_disclosure_body",
        source_record_id=old["source_document_id"],
        source_revision=CONTRACT_ID,
        source_artifact_sha256=body_sha256,
        knowledge_from=clock,
        knowledge_to=None,
        effective_at=published,
        announced_at=published,
    )
    rows = dict(
        dwd_disclosure=[
            dict(
                **common,
                disclosure_id=old["disclosure_id"],
                company_id=parent.company_id,
                disclosure_type="OFFICIAL_BODY_VERIFIED_DISCLOSURE",
                source_document_id=old["source_document_id"],
                title=actual["announcementTitle"],
                document_uri=uri,
                language="zh-CN",
            )
        ],
        dwd_disclosure_subject=[
            dict(
                **common,
                disclosure_subject_id="subject_"
                + digest([CONTRACT_ID, old["disclosure_id"], parent.security_id])[7:],
                disclosure_id=old["disclosure_id"],
                subject_type="SECURITY",
                security_id=parent.security_id,
                corporate_action_id=None,
                financial_report_id=None,
            )
        ],
    )
    for table, values in rows.items():
        if (
            pa.Table.from_pylist(values, schema=disclosure_event_schemas()[table]).to_pylist()
            != values
        ):
            raise BoundedError("DISCLOSURE_BODY_NATIVE_CLOCK_CHANGED")
    return rows, dict(
        contract_id=CONTRACT_ID,
        rows_sha256=digest(rows),
        source_body_sha256=body_sha256,
        interpreted_body_sha256=digest(body_text),
        original_selected_reference_sha256=digest(old),
        actual_index_row_sha256=digest(actual),
        original_index_sha256=selection["index_sha256"],
        actual_received_at=received,
        observed_at=clock,
        original_provider_acquisition=None,
        original_PIT_restored=False,
        weekly_fields_or_no_change_qualified=False,
        action_or_financial_or_transaction_terms_qualified=False,
        old_values_overwritten=False,
    )


def replay_body_domain(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("field_interpretation_qualified") is not False
    ):
        raise BoundedError("DISCLOSURE_BODY_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("DISCLOSURE_BODY_PHYSICAL_SET_CHANGED")
    for name, member in plan["members"].items():
        path = physical_path(root / name)
        if (
            root.resolve() not in path.parents
            or path.stat().st_nlink != 1
            or path.stat().st_size != member["bytes"]
            or file_sha(path) != member["sha256"]
        ):
            raise BoundedError("DISCLOSURE_BODY_MEMBER_CHANGED")
    for name, sha in plan["code_inputs"].items():
        if file_sha(Path(name)) != sha:
            raise BoundedError("DISCLOSURE_BODY_ORDINARY_CODE_CHANGED")
    manifest_path = root / "inputs/image-manifest.json"
    if (
        file_sha(manifest_path)
        != "sha256:91845422851368028e4e8a6b83e0dc9ba2a9e30dfdd8e4d6de892f64adac1d7f"
    ):
        raise BoundedError("DISCLOSURE_BODY_RETAINED_PARENT_CHANGED")
    manifest = json.loads(manifest_path.read_bytes())
    layout_path = root / "inputs/image-materialization.json"
    if file_sha(layout_path) != manifest["materialization_sha256"]:
        raise BoundedError("DISCLOSURE_BODY_PARENT_LAYOUT_CHANGED")
    layout = json.loads(layout_path.read_bytes())["layout"]
    parents: dict[str, list[dict[str, Any]]] = {}
    for table, members in plan["parents"].items():
        originals = {
            m["path"]: m["sha256"]
            for m in layout[table]["files"]
            if not table.startswith("dwd_disclosure")
            or "effective_at_year=2026/effective_at_month=9/" in m["path"]
        }
        if {m["original"] for m in members} != set(originals):
            raise BoundedError("DISCLOSURE_BODY_PARENT_PHYSICAL_SET_CHANGED")
        parents[table] = []
        for member in members:
            path = root / member["local"]
            if file_sha(path) != originals[member["original"]]:
                raise BoundedError("DISCLOSURE_BODY_NATIVE_PARENT_CHANGED")
            parents[table].extend(pq.ParquetFile(path).read().to_pylist())
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    # Every selected original reference is an actual retained disclosure version,
    # not just a caller-supplied company/code association.
    originals = {
        (r["disclosure_id"], r["company_id"], r["source_document_id"]): r
        for r in parents["dwd_disclosure"]
    }
    output: dict[str, list[dict[str, Any]]] = {
        t: [] for t in ["dwd_disclosure", "dwd_disclosure_subject"]
    }
    proofs = []
    for spec in plan["selection"]:
        selection = json.loads((root / spec["selection"]).read_bytes())["selection"]
        old = selection["original_disclosure"]
        native_old = originals.get(
            (old["disclosure_id"], old["company_id"], old["source_document_id"])
        )
        if native_old is None or digest(native_old) != old["source_row_sha256"]:
            raise BoundedError("DISCLOSURE_BODY_ORIGINAL_REFERENCE_NOT_RETAINED")
        references = [
            {k: r[k] for k in ["security_id", "corporate_action_id", "financial_report_id"]}
            for r in parents["dwd_disclosure_subject"]
            if r["disclosure_id"] == old["disclosure_id"]
        ]
        if sorted(map(digest, references)) != sorted(map(digest, old["explicit_references"])):
            raise BoundedError("DISCLOSURE_BODY_ORIGINAL_SUBJECT_REFERENCE_CHANGED")
        index_path = root / spec["index"]
        if file_sha(index_path) != selection["index_sha256"]:
            raise BoundedError("DISCLOSURE_BODY_ORIGINAL_INDEX_CHANGED")
        parent, _ = resolver.resolve_for_report(
            selection["actual_index_row"]["secCode"],
            period_end=date.fromisoformat(old["business_date"]),
        )
        if parent is None:
            raise BoundedError("DISCLOSURE_BODY_DATED_IDENTITY_MISSING")
        body = subprocess.run(
            ["pdftotext", "-layout", str(root / spec["PDF"]), "-"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout.decode()
        rows, proof = project_body(
            selection,
            json.loads((root / spec["receipt"]).read_bytes()),
            index=json.loads(index_path.read_bytes()),
            body_sha256=file_sha(root / spec["PDF"]),
            body_text=body,
            parent=parent,
            observed_at=datetime.fromisoformat(plan["observed_at"]),
        )
        for table, values in rows.items():
            output[table].extend(values)
        proofs.append(proof)
    contract, _, _ = load_contract(root / "config")
    for table, values in output.items():
        physical = pq.ParquetFile(root / "outputs" / (table + ".parquet")).read()
        keys = contract["tables"][table]["version_key"]
        if (
            physical.schema != disclosure_event_schemas()[table]
            or physical.to_pylist() != values
            or len({digest([r[k] for k in keys]) for r in values}) != len(values)
        ):
            raise BoundedError("DISCLOSURE_BODY_NATIVE_RECONSTRUCTION_CHANGED")
    if digest(output) != plan["semantic_sha256"] or digest(proofs) != plan["proof_sha256"]:
        raise BoundedError("DISCLOSURE_BODY_SEMANTIC_CHANGED")
    return dict(
        status="PASS_FULL_LOCAL_ORIGINAL_DISCLOSURE_BODY_REPLAY",
        rows={t: len(v) for t, v in output.items()},
        input_identity=plan["identity"],
        complete_Base=False,
        field_interpretation_qualified=False,
        consumer_admission="NOT_GRANTED",
        old_source_roots_or_network_required=False,
    )
