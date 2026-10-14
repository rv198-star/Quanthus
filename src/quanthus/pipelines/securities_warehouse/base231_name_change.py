"""Official dated short-name aliases, without changing code or trading state."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .financial_reporting import FinancialCompanyIdentity

CONTRACT_ID = "p10-base231-official-dated-stock-short-name-v1"
_DAY = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
_NAMES = r"[“\"]([^”\"]{1,30})[”\"]变更为[“\"]([^”\"]{1,30})[”\"]"


def reconcile_name_intervals(
    rows: list[dict[str, Any]], proofs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(rows) != len(proofs):
        raise BoundedError("OFFICIAL_NAME_COMPLETE_CHAIN_REQUIRED")
    result = [dict(row) for row in rows]
    groups: dict[tuple[Any, ...], list[int]] = {}
    for n, row in enumerate(rows):
        groups.setdefault((row["security_id"], row["knowledge_from"]), []).append(n)
    for positions in groups.values():
        ordered = sorted(positions, key=lambda n: rows[n]["effective_from"])
        for a, b in zip(ordered, ordered[1:], strict=False):
            if rows[a]["effective_from"] >= rows[b]["effective_from"] or (
                proofs[b]["original_pair"]["old_name"] != rows[a]["identifier_value"]
            ):
                raise BoundedError("OFFICIAL_NAME_SOURCE_CHAIN_CONFLICT")
            result[a]["effective_to"] = rows[b]["effective_from"]
    return result


def parse_name_change(body: str, *, code: str) -> dict[str, Any]:
    if not isinstance(body, str) or not 0 < len(body.encode()) <= 4 * 1024**2:
        raise BoundedError("OFFICIAL_NAME_BODY_BOUND")
    text = re.sub(r"\s+", "", body)
    if not re.fullmatch(r"\d{6}", code) or not re.search(
        r"(?:证券|股票)代码[:：]" + code + r"(?![0-9])", text[:3000]
    ):
        raise BoundedError("OFFICIAL_NAME_EXACT_HEADER_CODE_REQUIRED")
    dated = set(re.findall(r"(?:证券|股票)(?:证券)?简称将[于自]" + _DAY + r"起由" + _NAMES, text))
    pairs = set(re.findall(r"(?:证券|股票)(?:证券)?简称(?:将)?由" + _NAMES, text))
    before = set(
        re.findall(
            r"(?m)^\s*(?:[一二三四\d]+[、.]\s*)?变更前证券简称[:：]"
            r"\s*([^\s；;：:]{1,30})\s*$",
            body,
        )
    )
    after = set(
        re.findall(
            r"(?m)^\s*(?:[一二三四\d]+[、.]\s*)?变更后证券简称[:：]"
            r"\s*([^\s；;：:]{1,30})\s*$",
            body,
        )
    )
    if before or after:
        if len(before) != len(after) or len(before) != 1:
            raise BoundedError("OFFICIAL_NAME_EXPLICIT_PAIR_CONFLICT")
        pairs.add((next(iter(before)), next(iter(after))))
    date_tokens = re.findall(
        r"(?:证券简称变更日期|(?:新证券简称的|证券简称)启用日期|"
        r"(?:撤销|实施)[^。；：:]{0,30}风险警示(?:的)?起始日)(?:为|[:：])" + _DAY,
        text,
    )
    activation = re.findall(r"(?:证券|股票)简称由" + _NAMES + r"[,，]启用时间为" + _DAY, text)
    date_tokens.extend(tuple(x[2:]) for x in activation)
    labelled_dates = {date(*map(int, tokens)) for tokens in date_tokens}
    if dated:
        if len(dated) != 1:
            raise BoundedError("OFFICIAL_NAME_EXPLICIT_DATE_CONFLICT")
        y, m, d, old, new = next(iter(dated))
        day = date(int(y), int(m), int(d))
        if pairs and pairs != {(old, new)} or labelled_dates and labelled_dates != {day}:
            raise BoundedError("OFFICIAL_NAME_EXPLICIT_DATE_CONFLICT")
    elif len(pairs) == len(labelled_dates) == 1:
        old, new = next(iter(pairs))
        day = next(iter(labelled_dates))
    else:
        raise BoundedError("OFFICIAL_NAME_IMPLEMENTED_PAIR_AND_DATE_UNPROVEN")
    if old == new or day > date(2026, 9, 30):
        raise BoundedError("OFFICIAL_NAME_BUSINESS_SCOPE_UNQUALIFIED")
    return dict(
        code=code,
        old_name=old,
        new_name=new,
        effective_date=day,
        source_text_sha256=digest(body),
        field="STOCK_SHORT_NAME_ONLY",
    )


def project_name_change(
    body: str,
    *,
    code: str,
    parent: FinancialCompanyIdentity,
    venue_id: str,
    PDF_sha256: str,
    announcement_id: str,
    received_at: datetime,
    observed_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    import pyarrow as pa  # type: ignore[import-untyped]

    from .listed_company import _table_schemas

    parsed = parse_name_change(body, code=code)
    effective = instant(
        datetime.combine(parsed["effective_date"], time(), ZoneInfo("Asia/Shanghai"))
    )
    clock = instant(observed_at)
    if (
        parent.stock_code != code
        or not venue_id
        or not parent.effective_from <= effective
        or parent.effective_to is not None
        and effective >= parent.effective_to
        or parent.knowledge_from > clock
        or instant(received_at) > clock
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", PDF_sha256)
        or not re.fullmatch(r"\d+", announcement_id)
    ):
        raise BoundedError("OFFICIAL_NAME_PARENT_SOURCE_OR_CLOCK_UNQUALIFIED")
    row = dict(
        record_source="cninfo.official_dated_stock_short_name",
        source_record_id=announcement_id,
        source_revision=CONTRACT_ID,
        source_artifact_sha256=PDF_sha256,
        knowledge_from=clock,
        knowledge_to=None,
        effective_from=effective,
        effective_to=None,
        security_id=parent.security_id,
        identifier_namespace="CNINFO_OFFICIAL_STOCK_SHORT_NAME",
        identifier_value=parsed["new_name"],
        venue_id=venue_id,
    )
    schema = _table_schemas()["dwd_security_identifier"]
    native = pa.Table.from_pylist([row], schema=schema).to_pylist()[0]
    if native != row:
        raise BoundedError("OFFICIAL_NAME_NATIVE_VALUE_CHANGED")
    return native, dict(
        contract_id=CONTRACT_ID,
        original_pair=parsed,
        typed_row_sha256=digest(native),
        code_identity_changed=False,
        listing_or_trading_state_changed=False,
        historical_knowledge_inferred=False,
        consumer_admission="NOT_GRANTED",
    )


def replay_name_domain(root: Path) -> dict[str, Any]:
    """Rebuild each accepted alias from its local original PDF/index/parents."""
    import subprocess

    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import file_sha, load_contract
    from .base22_sources import _tree, physical_path
    from .financial_reporting import financial_company_resolver_from_listed_company_rows
    from .listed_company import _table_schemas

    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("historical_knowledge_inferred") is not False
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("OFFICIAL_NAME_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("OFFICIAL_NAME_PHYSICAL_SET_CHANGED")
    for name, member in plan["members"].items():
        p = physical_path(root / name)
        if (
            root.resolve() not in p.parents
            or p.stat().st_nlink != 1
            or (p.stat().st_size != member["bytes"] or file_sha(p) != member["sha256"])
        ):
            raise BoundedError("OFFICIAL_NAME_LOCAL_MEMBER_CHANGED")
    for name, sha in plan["code_inputs"].items():
        if file_sha(Path(name)) != sha:
            raise BoundedError("OFFICIAL_NAME_ORDINARY_CODE_CHANGED")
    if file_sha(root / "inputs/image-manifest.json") != (
        "sha256:91845422851368028e4e8a6b83e0dc9ba2a9e30dfdd8e4d6de892f64adac1d7f"
    ):
        raise BoundedError("OFFICIAL_NAME_NATIVE_PARENT_UNQUALIFIED")
    manifest = json.loads((root / "inputs/image-manifest.json").read_bytes())
    materialization = root / "inputs/image-materialization.json"
    if file_sha(materialization) != manifest["materialization_sha256"]:
        raise BoundedError("OFFICIAL_NAME_PARENT_LAYOUT_CHANGED")
    layout = json.loads(materialization.read_bytes())["layout"]
    parents: dict[str, list[dict[str, Any]]] = {}
    if set(plan["parents"]) != {"dwd_security", "dwd_security_identifier", "dwd_listing"}:
        raise BoundedError("OFFICIAL_NAME_EXACT_PARENT_TABLES_REQUIRED")
    for table, entries in plan["parents"].items():
        if {s["original"] for s in entries} != {s["path"] for s in layout[table]["files"]}:
            raise BoundedError("OFFICIAL_NAME_EXACT_PARENT_FILE_SET_CHANGED")
        expected = {s["path"]: s["sha256"] for s in layout[table]["files"]}
        parents[table] = []
        for spec in entries:
            if file_sha(root / spec["local"]) != expected[spec["original"]]:
                raise BoundedError("OFFICIAL_NAME_NATIVE_PARENT_CHANGED")
            parents[table].extend(pq.ParquetFile(root / spec["local"]).read().to_pylist())
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    now = datetime.fromisoformat(plan["observed_at"])
    captures = []
    for spec in plan["captures"]:
        registration = root / spec["registration"]
        execution = json.loads((root / spec["execution"]).read_bytes())
        if execution["registration_sha256"] != file_sha(registration):
            raise BoundedError("OFFICIAL_NAME_CAPTURE_REGISTRATION_CHANGED")
        targets = {
            r["code"].split(".")[-1]: r for r in json.loads(registration.read_bytes())["targets"]
        }
        captures.append((targets, execution["selected"]))
    rows, proofs = [], []
    for spec in plan["selection"]:
        source = json.loads((root / spec["selection"]).read_bytes())
        if not any(
            source in selected
            and source["code"] in targets
            and source["original_difference_sha256"] == digest(targets[source["code"]])
            for targets, selected in captures
        ):
            raise BoundedError("OFFICIAL_NAME_CAPTURE_SOURCE_MEMBERSHIP_UNPROVEN")
        actual = source["actual_index_row"]
        index = json.loads((root / spec["index"]).read_bytes())
        receipt = json.loads((root / spec["receipt"]).read_bytes())
        uri = "https://static.cninfo.com.cn/" + str(actual.get("adjunctUrl"))
        received = datetime.fromisoformat(receipt["completed_at"])
        if (
            source["code"] != actual.get("secCode")
            or actual not in (index.get("announcements") or [])
            or source["index_sha256"] != file_sha(root / spec["index"])
            or source["receipt"] != receipt
            or receipt["URI"] != uri
            or receipt["http_status"] != "200"
            or receipt["curl_exit"] != 0
            or receipt["body_sha256"] != file_sha(root / spec["PDF"])
            or type(actual.get("announcementTime")) is not int
            or actual["announcementTime"] > received.timestamp() * 1000
            or not re.fullmatch(
                r"finalpage/\d{4}-\d{2}-\d{2}/[0-9]+\.[Pp][Dd][Ff]", str(actual.get("adjunctUrl"))
            )
        ):
            raise BoundedError("OFFICIAL_NAME_ORIGINAL_INDEX_RECEIPT_CHANGED")
        body = subprocess.run(
            ["pdftotext", "-layout", str(root / spec["PDF"]), "-"],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout.decode()
        parsed = parse_name_change(body, code=source["code"])
        parent, _ = resolver.resolve_for_report(source["code"], period_end=parsed["effective_date"])
        if parent is None:
            raise BoundedError("OFFICIAL_NAME_DATED_IDENTITY_UNQUALIFIED")
        effective = instant(
            datetime.combine(parsed["effective_date"], time(), ZoneInfo("Asia/Shanghai"))
        )
        venues = {
            r["venue_id"]
            for r in parents["dwd_listing"]
            if (
                r["security_id"] == parent.security_id
                and r["effective_from"] <= effective
                and (r["effective_to"] is None or effective < r["effective_to"])
                and r["knowledge_from"] <= now
                and (r["knowledge_to"] is None or now < r["knowledge_to"])
            )
        }
        if len(venues) != 1:
            raise BoundedError("OFFICIAL_NAME_DATED_VENUE_UNQUALIFIED")
        row, proof = project_name_change(
            body,
            code=source["code"],
            parent=parent,
            venue_id=next(iter(venues)),
            PDF_sha256=file_sha(root / spec["PDF"]),
            announcement_id=str(actual["announcementId"]),
            received_at=received,
            observed_at=now,
        )
        rows.append(row)
        proofs.append(proof)
    rows = reconcile_name_intervals(rows, proofs)
    table = "dwd_security_identifier"
    native = root / "outputs/dwd_security_identifier.parquet"
    if list((root / "outputs").iterdir()) != [native] or (
        pq.ParquetFile(native).schema_arrow != _table_schemas()[table]
        or pq.ParquetFile(native).read().to_pylist() != rows
        or digest(rows) != plan["semantic_sha256"]
        or digest(proofs) != plan["proof_sha256"]
    ):
        raise BoundedError("OFFICIAL_NAME_FULL_SERIALIZED_REPLAY_CHANGED")
    contract, _, _ = load_contract(root / "config")
    keys = contract["tables"][table]["version_key"]
    if len({digest([r[k] for k in keys]) for r in rows}) != len(rows):
        raise BoundedError("OFFICIAL_NAME_DUPLICATE_VERSION")
    if len({(r["security_id"], r["effective_from"]) for r in rows}) != len(rows):
        raise BoundedError("OFFICIAL_NAME_CONFLICTING_DATED_ALIASES")
    return dict(
        status="PASS_FULL_LOCAL_OFFICIAL_NAME_REPLAY",
        rows=len(rows),
        identity=plan["identity"],
        semantic_sha256=digest(rows),
        complete_Base=False,
        code_identity_changed=False,
        source_network_or_old_roots_required=False,
        consumer_admission="NOT_GRANTED",
    )
