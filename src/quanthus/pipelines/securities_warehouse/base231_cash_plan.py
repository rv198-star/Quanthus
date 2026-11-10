"""Explicit complete SSE cash schemes under the separately retained rule.

Only the combined share-change input is zero for this enumerated cash scheme.
Unreported bonus/transfer components remain null; old source observations and
consumer methods are untouched. This is not a cumulative provider adjustment.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base231_event_factor import CONTRACT_ID as ENGINE_CONTRACT

CONTRACT_ID = "p10-base231-official-sse-complete-cash-plan-reference-input-v1"
RULE_URI = (
    "https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/"
    "10816492/files/58bd5ce4dfc94b57a5391f60eb2719ab.docx"
)
FORMULA = (
    "除权（息）参考价格＝[（前收盘价格-现金红利）+配股价格×流通股份变动比例]"
    "/（1+流通股份变动比例）。"
)


def cash_plan_inputs(
    record: dict[str, Any],
    proof: dict[str, Any],
    *,
    official_body: str,
    rule_body: str,
    rule_member_sha256: str,
    rule_URI: str,
    rule_effective_date: date,
) -> dict[str, Any]:
    text = re.sub(r"\s+", "", official_body)
    rule = re.sub(r"\s+", "", rule_body)
    fields = ("cash_per_share", "bonus_shares", "transfer_shares")
    values = {k: record[k] for k in fields}
    if (
        proof.get("source_body_sha256") != digest(official_body)
        or proof.get("basis") != "ALL_ISSUED_PRE_EVENT_SHARES"
        or proof.get("method") != "EXPLICIT_ALL_ISSUED_IMPLEMENTATION_BASE"
        or proof.get("code") != record.get("stock_code")
        or proof.get("ex_date") != record.get("ex_date")
        or proof.get("coefficients_sha256") != digest(values)
        or record.get("action_type") != "cash_div"
        or values["bonus_shares"] is not None
        or values["transfer_shares"] is not None
        or rule_URI != RULE_URI
        or rule_effective_date != date(2026, 7, 6)
        or record["ex_date"] < rule_effective_date
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", rule_member_sha256)
        or rule.count(FORMULA) != 1
        or rule.count("4.3.2除权（息）参考价格的计算公式为：") != 1
        or "差异化分红送转：否" not in text
    ):
        raise BoundedError("COMPLETE_CASH_PLAN_OFFICIAL_RULE_OR_SOURCE_UNPROVEN")
    sections = re.findall(r"二、分配方案(.+?)三、相关日期", text)
    if len(sections) != 1:
        raise BoundedError("COMPLETE_CASH_PLAN_UNIQUE_IMPLEMENTATION_SECTION_REQUIRED")
    section = sections[0]
    plans = re.findall(
        r"3[.．]分配方案[：:]本次利润分配以方案实施前的公司总股本"
        r"([0-9,]+)股为基数，每股派发现金红利([0-9.]+)元（含税），"
        r"共计派发现金红利([0-9,.]+)元（含税）[。.]",
        section,
    )
    if len(plans) != 1:
        raise BoundedError("COMPLETE_CASH_PLAN_ENUMERATED_CASH_SCHEME_REQUIRED")
    total_token, cash_token, amount_token = plans[0]
    # Bind the complete remaining scheme. The one permitted suffix concerns
    # already paid interim cash and the declared annual cash total, never shares.
    sentence = re.search(r"3[.．]分配方案[：:].+?元（含税）[。.]", section)
    assert sentence is not None
    suffix = section[sentence.end() :]
    if suffix and not re.fullmatch(
        r"连同公司已于[0-9]{4}年[0-9]{1,2}月向全体股东派发的[0-9]{4}年中期现金红利"
        r"[0-9,.]+元（每股派发现金红利人民币[0-9.]+元，含税），公司[0-9]{4}年度全年拟派发"
        r"现金红利总额为人民币[0-9,.]+元（含税）[。.]",
        suffix,
    ):
        raise BoundedError("COMPLETE_CASH_PLAN_ADDITIONAL_SCHEME_UNPROVEN")
    shares = Decimal(total_token.replace(",", ""))
    cash, amount = Decimal(cash_token), Decimal(amount_token.replace(",", ""))
    with localcontext() as ctx:
        ctx.prec = 96
        if (
            shares <= 0
            or cash < 0
            or cash != record["cash_per_share"]
            or (shares * cash).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            != amount.quantize(Decimal("0.01"))
        ):
            raise BoundedError("COMPLETE_CASH_PLAN_CASH_BASE_OR_TOTAL_CONFLICT")
    return dict(
        contract_id=ENGINE_CONTRACT,
        source_input_contract_id=CONTRACT_ID,
        code=record["stock_code"],
        ex_date=record["ex_date"],
        cash_per_share=cash,
        combined_share_increment=Decimal(0),
        official_proof_sha256=digest(proof),
        original_record_sha256=digest(record),
        unknown_components=[k for k, v in values.items() if v is None],
        component_nulls_preserved=True,
        complete_cash_scheme_source_qualified=True,
        basis=proof["basis"],
        scheme_sha256=digest(section),
        rule_body_sha256=digest(rule_body),
        rule_member_sha256=rule_member_sha256,
        rule_URI=rule_URI,
        rule_effective_date=rule_effective_date,
        historical_PIT_qualified=False,
        consumer_admission="NOT_GRANTED",
    )


def replay_cash_domain(root: Any) -> dict[str, Any]:
    """Reconstruct the bounded cash-plan output from local native dependencies."""
    import json
    import subprocess
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import file_sha, load_contract, schemas
    from .base22_sources import _tree, physical_path
    from .base231_event_factor import factor_observation
    from .base231_official_adjustment import parse_adjustment

    root = Path(root)
    plan = json.loads((root / "INPUTS.json").read_bytes())
    if (
        plan.get("contract_id") != CONTRACT_ID
        or plan.get("through_date") != "2026-09-30"
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
    ):
        raise BoundedError("CASH_PLAN_OFFLINE_PURPOSE_CHANGED")
    if _tree(root) - {"INPUTS.json", "DOMAIN-REPLAY.json", "READY"} != set(plan["members"]):
        raise BoundedError("CASH_PLAN_OFFLINE_PHYSICAL_SET_CHANGED")
    for name, spec in plan["members"].items():
        path = physical_path(root / name)
        if (
            root.resolve() not in path.parents
            or path.stat().st_nlink != 1
            or path.stat().st_size != spec["bytes"]
            or file_sha(path) != spec["sha256"]
        ):
            raise BoundedError("CASH_PLAN_OFFLINE_LOCAL_MEMBER_CHANGED")
    for path, sha in plan["code_inputs"].items():
        if file_sha(Path(path)) != sha:
            raise BoundedError("CASH_PLAN_OFFLINE_CODE_CHANGED")
    parent_path = root / "inputs/prior-INPUTS.json"
    if (
        file_sha(parent_path)
        != "sha256:8c192fdd711c38233c46ea2f00fc7f7e3215ac4dfef525cfb8903b40a7291be9"
    ):
        raise BoundedError("CASH_PLAN_EXACT_PREVIOUS_QUALIFIED_CAPSULE_CHANGED")
    parent = json.loads(parent_path.read_bytes())
    native = sorted(n for n in parent["members"] if n.startswith("payload/"))
    if sorted(n for n in plan["members"] if n.startswith("payload/")) != native:
        raise BoundedError("CASH_PLAN_NATIVE_DEPENDENCY_SET_CHANGED")
    for name in native + [
        "official/inputs/601390/official.pdf",
        "official/outputs/dwd_corporate_action.parquet",
    ]:
        if file_sha(root / name) != parent["members"][name]["sha256"]:
            raise BoundedError("CASH_PLAN_PRIOR_NATIVE_DEPENDENCY_CHANGED")
    selection = [r for r in parent["selection"] if r["code"] == "601390"]
    if len(selection) != 1 or selection[0]["venue"] != "SSE":
        raise BoundedError("CASH_PLAN_EXACT_SOURCE_EVENT_UNPROVEN")
    spec = selection[0]
    body = subprocess.run(
        ["pdftotext", "-layout", str(root / "official" / spec["pdf"]), "-"],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout.decode()
    record, proof = parse_adjustment(
        body,
        code=spec["code"],
        venue=spec["venue"],
        expected_ex_date=date.fromisoformat(spec["ex_date"]),
    )
    # Reconstruct the actual complete DOCX, rather than trust a derived text file.
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(root / "inputs/sse-rule.docx") as archive:
        if sum(m.file_size for m in archive.infolist()) > 16 * 1024**2:
            raise BoundedError("CASH_PLAN_RULE_XML_EXPANSION_BOUND")
        xml = ET.fromstring(archive.read("word/document.xml"))
    rule = "\n".join(
        "".join(p.itertext())
        for p in xml.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
    )
    receipt = json.loads((root / "inputs/rule-receipt.json").read_bytes())
    if (
        receipt.get("URI") != RULE_URI
        or receipt.get("http_status") != "200"
        or receipt.get("curl_exit") != 0
        or receipt.get("body_sha256") != file_sha(root / "inputs/sse-rule.docx")
        or file_sha(root / "inputs/sse-rule.docx")
        != "sha256:fc922c433438b2636cb631eab25cca405209712acbb6aaded768c45456ff8888"
    ):
        raise BoundedError("CASH_PLAN_REAL_RULE_RECEIPT_UNPROVEN")
    now = datetime.fromisoformat(plan["observed_at"])
    if datetime.fromisoformat(receipt["completed_at"]) > now:
        raise BoundedError("CASH_PLAN_RULE_NOT_AVAILABLE")
    inputs = cash_plan_inputs(
        record,
        proof,
        official_body=body,
        rule_body=rule,
        rule_member_sha256=file_sha(root / "inputs/sse-rule.docx"),
        rule_URI=RULE_URI,
        rule_effective_date=date(2026, 7, 6),
    )
    parents = {
        t: [
            r
            for path in (root / "payload" / t).rglob("*.parquet")
            for r in pq.ParquetFile(path).read().to_pylist()
        ]
        for t in ["dwd_listing", "dwd_trading_calendar", "dwd_trading_venue"]
    }
    listings = {r["listing_id"]: r for r in parents["dwd_listing"]}
    venues = [r for r in parents["dwd_trading_venue"] if r["mic_code"] == "XSHG"]
    if len(venues) != 1:
        raise BoundedError("CASH_PLAN_UNIQUE_SSE_VENUE_REQUIRED")
    sessions = [
        r
        for r in parents["dwd_trading_calendar"]
        if r["venue_id"] == venues[0]["venue_id"]
        and r["is_open"]
        and r["session_type"] in {"REGULAR_DAY", "day"}
        and r["trade_date"] < record["ex_date"]
        and r["knowledge_from"] <= now
        and (r["knowledge_to"] is None or now < r["knowledge_to"])
    ]
    prior = max(r["trade_date"] for r in sessions)
    session_rows = [r for r in sessions if r["trade_date"] == prior]
    if len(session_rows) != 1:
        raise BoundedError("CASH_PLAN_UNIQUE_PRECEDING_SESSION_REQUIRED")
    session = session_rows[0]
    candidates = []
    for path in sorted((root / "payload/dwd_security_bar").rglob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=16384):
            for row in batch.to_pylist():
                listing = listings[row["listing_id"]]
                if (
                    listing["security_id"] == spec["security_id"]
                    and listing["venue_id"] == session["venue_id"]
                    and row["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date()
                    == prior
                    and row["price_basis"] == "UNADJUSTED"
                    and row["interval_size"] == "1d"
                    and row["calendar_session_id"] == session["calendar_session_id"]
                    and row["knowledge_from"] <= now
                    and (row["knowledge_to"] is None or now < row["knowledge_to"])
                ):
                    candidates.append((row, file_sha(path)))
    latest = max(r["knowledge_from"] for r, _ in candidates)
    candidates = [(r, sha) for r, sha in candidates if r["knowledge_from"] == latest]
    if len(candidates) != 1:
        raise BoundedError("CASH_PLAN_UNIQUE_PRECEDING_BAR_REQUIRED")
    bar, bar_sha = candidates[0]
    actions = [
        r
        for r in pq.ParquetFile(root / "official/outputs/dwd_corporate_action.parquet")
        .read()
        .to_pylist()
        if r["security_id"] == spec["security_id"]
    ]
    if len(actions) != 1:
        raise BoundedError("CASH_PLAN_UNIQUE_OFFICIAL_ACTION_REQUIRED")
    rows, evidence = factor_observation(
        inputs,
        prior_close=bar["close"],
        prior_business_date=prior,
        expected_prior_session=session["trade_date"],
        price_basis=bar["price_basis"],
        currency=bar["currency"],
        security_id=spec["security_id"],
        action_id=actions[0]["corporate_action_id"],
        observed_at=now,
        source_known_at=actions[0]["knowledge_from"],
        price_known_at=bar["knowledge_from"],
        source_sha256=file_sha(root / "official" / spec["pdf"]),
        price_row_sha256=digest(bar),
        price_member_sha256=bar_sha,
    )
    _, model, extension = load_contract(root / "config")
    expected_schema = schemas(model, extension)
    result = {}
    for table, values in rows.items():
        typed = pa.Table.from_pylist(values, schema=expected_schema[table])
        actual = pq.ParquetFile(root / "outputs" / (table + ".parquet")).read()
        if actual.schema != typed.schema or actual.to_pylist() != typed.to_pylist():
            raise BoundedError("CASH_PLAN_NATIVE_OUTPUT_CHANGED")
        result[table] = typed.num_rows
    if digest(evidence) != plan["output_proof_sha256"]:
        raise BoundedError("CASH_PLAN_FULL_INPUT_OUTPUT_CLOSURE_CHANGED")
    return dict(
        status="PASS_FULL_LOCAL_CASH_PLAN_NATIVE_REPLAY",
        rows=result,
        input_identity=plan["identity"],
        unknown_components=inputs["unknown_components"],
        native_source_network_or_old_roots_required=False,
        complete_Base=False,
        consumer_admission="NOT_GRANTED",
        historical_PIT_qualified=False,
    )
