"""Exact official-body adjudication of a stale equity status claim (P10 #538)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .base22 import digest, file_sha
from .base24_execution_evidence import CONTRACT, _require, pdf_text
from .base24_status_evidence import _instant


def adjudicate_body(text: str, selection: dict[str, Any], target: date) -> None:
    """A frozen exact-body selection, never a title or carry-forward NORMAL label.

    Conflicting dates are retained as a conservative closed bound; no particular
    historical effective instant is invented. All cited dates must precede target.
    """
    text = re.sub(r"\s+", "", text)
    _require(selection["code"] in text, "NOTICE_SECURITY")
    _require(selection["disposition"] == "WITHDRAW_ST_ASSERTION", "NOTICE_DISPOSITION")
    _require(selection["transition"] in text, "NOTICE_TRANSITION")
    _require(re.fullmatch(r"[^*＊]*ST.*", selection["new_name"]) is None, "PARTIAL_WITHDRAWAL")
    _require("ST" not in selection["new_name"].upper(), "PARTIAL_WITHDRAWAL")
    _require(selection["new_name"] in selection["transition"], "NOTICE_TRANSITION")
    _require(
        "变更为" in selection["transition"] and "ST" in selection["transition"], "NOTICE_TRANSITION"
    )
    _require(selection["operative_anchor"] in text, "NOTICE_OPERATIVE_EVIDENCE")
    _require(selection["date_anchors"], "NOTICE_EFFECTIVE_BOUND")
    for item in selection["date_anchors"]:
        d = date.fromisoformat(item["day"])
        expected = f"{d.year}年{d.month}月{d.day}日"
        _require(item["text"] in text and expected in item["text"], "NOTICE_EFFECTIVE_BOUND")
        _require(d <= target, "NOTICE_NOT_YET_EFFECTIVE")


def withdraw_notice_claim(
    root: Path,
    selection: dict[str, Any],
    native: dict[str, Any],
    raw: dict[str, Any],
    event: dict[str, Any],
    *,
    repair_at: datetime,
    raw_sha256: str,
    target: date,
) -> dict[str, Any]:
    ident = str(event["ann_id"])
    _require(
        selection["id"] == ident and selection["code"] == raw["stock_code"] == event["stock_code"],
        "NOTICE_IDENTITY",
    )
    query = parse_qs(urlparse(raw["source_url_or_notice_id"]).query)
    _require(
        query.get("announcementId") == [ident]
        and query.get("stockCode") == [selection["code"]]
        and raw["announcement_date"] == event["announcement_date"],
        "NOTICE_RAW_LINK",
    )
    _require(
        native["status_type"] == "SPECIAL_TREATMENT"
        and native["source_artifact_sha256"] == raw_sha256
        and native["source_record_id"]
        == "payload/canonical/ref_st_status_pit.parquet#interval_id=" + raw["interval_id"],
        "NOTICE_NATIVE_LINK",
    )
    body = root / ("notice-pdf-" + ident + ".raw")
    receipt = json.loads((root / ("notice-pdf-" + ident + ".receipt.json")).read_bytes())
    index = json.loads((root / ("notice-index-" + ident + ".raw")).read_bytes())
    matches = [
        x
        for x in index["announcements"]
        if str(x["announcementId"]) == ident and x["secCode"] == selection["code"]
    ]
    _require(len(matches) == 1, "NOTICE_INDEX_IDENTITY")
    _require(
        receipt["URI"] == "https://static.cninfo.com.cn/" + matches[0]["adjunctUrl"]
        and receipt["http_status"] == 200
        and receipt["error"] is None,
        "NOTICE_OFFICIAL_RECEIPT",
    )
    _require(
        file_sha(body) == receipt["body_sha256"] == selection["body_sha256"], "NOTICE_BODY_HASH"
    )
    known = _instant(receipt["completed_at"])
    _require(known <= repair_at and repair_at.utcoffset() is not None, "NOTICE_KNOWLEDGE")
    adjudicate_body(pdf_text(body), selection, target)
    proof = dict(
        contract_id=CONTRACT,
        native=native,
        raw=raw,
        event=event,
        selection=selection,
        target=target,
        body_sha256=selection["body_sha256"],
        acquired_at=known,
        provider_acquired_at=None,
        repair_known_at=repair_at,
        successor_value="UNKNOWN",
        reason="OFFICIAL_BODY_WITHDRAWS_ST_ASSERTION",
        # This is not a continuous NORMAL interval or an assertion that no later event exists.
        original_source_unchanged=True,
        historical_pit_granted=False,
        consumer_admission_granted=False,
    )
    return {**proof, "identity": digest(proof)}
