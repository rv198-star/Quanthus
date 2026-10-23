"""Purpose-scoped declared session clocks, distinct from observed source times.

No fetch, native row rewrite or historical availability claim. The exact archived
2026 exchange rules qualify the regular daily business clock only from July 6.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
CONTRACT = "p10-declared-daily-session-clock.v1"
RULES = {
    "XSHG": {
        "body": "sse-rule-docx.raw",
        "receipt": "sse-rule-docx.receipt.json",
        "sha256": "sha256:fc922c433438b2636cb631eab25cca405209712acbb6aaded768c45456ff8888",
        "receipt_sha256": "sha256:26eb37cf08d85b140ab1d9666f8c1474840ec50cafe985debe17222992906310",
        "url": "https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/10816492/files/58bd5ce4dfc94b57a5391f60eb2719ab.docx",
        "section": "2.4.2",
        "effective_clause": "11.9本规则自2026年7月6日起施行。",
    },
    "XSHE": {
        "body": "szse-rule.raw",
        "receipt": "szse-rule.receipt.json",
        "sha256": "sha256:9b66f8b0db70f84a25ef1ccb4ee2351001724e408117552d75f6d8993483c586",
        "url": "https://www.szse.cn/lawrules/rule/trade/current/W020260424690713155663.pdf",
        "section": "2.3.2",
        "effective_clause": "10.9本规则自2026年7月6日起施行。",
    },
}
CLAUSE = (
    "证券采用竞价交易方式的，每个交易日的9:15至9:25为开盘集合竞价时间，"
    "9:30至11:30、13:00至14:57为连续竞价时间，14:57至15:00为收盘集合竞价时间。"
)


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    _require(result.utcoffset() is not None, "SCHEDULE_AWARE_TIME_REQUIRED")
    return result


def qualify_rules(root: Path, cutoff: datetime) -> dict[str, dict[str, Any]]:
    _require(cutoff.utcoffset() is not None, "SCHEDULE_AWARE_TIME_REQUIRED")
    result = {}
    for mic, spec in RULES.items():
        body, receipt = root / spec["body"], root / spec["receipt"]
        _require(not body.is_symlink() and not receipt.is_symlink(), "SCHEDULE_SOURCE_LINK")
        _require(sha(body) == spec["sha256"], "SCHEDULE_BODY_HASH")
        r = json.loads(receipt.read_bytes())
        if "receipt_sha256" in spec:
            _require(sha(receipt) == spec["receipt_sha256"], "SCHEDULE_RECEIPT_HASH")
        _require(
            r.get("URI") == spec["url"]
            and str(r.get("http_status")) == "200"
            and r.get("body_sha256") == spec["sha256"]
            and r.get("bytes") == body.stat().st_size
            and r.get("curl_exit", 0) == 0
            and r.get("error") is None,
            "SCHEDULE_RECEIPT_BINDING",
        )
        acquired = _instant(r["completed_at"])
        _require(_instant(r["started_at"]) <= acquired <= cutoff, "SCHEDULE_NOT_AVAILABLE")
        if mic == "XSHG":
            with zipfile.ZipFile(body) as z:
                text = "".join(ElementTree.fromstring(z.read("word/document.xml")).itertext())
        else:
            text = subprocess.run(
                ["pdftotext", "-layout", str(body), "-"],
                capture_output=True,
                check=True,
                timeout=30,
            ).stdout.decode("utf-8")
        text = re.sub(r"\s+", "", text)
        _require(
            spec["section"] + CLAUSE in text and spec["effective_clause"] in text,
            "SCHEDULE_OFFICIAL_CLAUSE",
        )
        result[mic] = {
            "mic": mic,
            "body_sha256": sha(body),
            "receipt_sha256": sha(receipt),
            "acquired_at": acquired.isoformat(),
            "effective_from": "2026-07-06",
            "section": spec["section"],
            "evidence_kind": "official_declared_schedule",
        }
    return result


def derive_session(
    row: Mapping[str, Any],
    *,
    mic: str,
    rule: Mapping[str, Any],
    calendar_sha256: str,
    qualified_at: datetime,
) -> dict[str, Any]:
    """Never claim 09:30 is the opening-auction instant or a realized fill."""
    _require(
        mic in RULES
        and rule.get("mic") == mic
        and rule.get("body_sha256") == RULES[mic]["sha256"]
        and rule.get("evidence_kind") == "official_declared_schedule",
        "SCHEDULE_RULE_IDENTITY",
    )
    day = row["trade_date"]
    _require(
        type(day) is date and date(2026, 7, 6) <= day <= date(2026, 9, 18),
        "SCHEDULE_BUSINESS_SCOPE",
    )
    _require(
        row["is_open"] is True
        and row["session_type"] in {"day", "REGULAR_DAY"}
        and row["open_at"] is None
        and row["close_at"] is None,
        "SCHEDULE_EXACT_MISSING_OPEN_ROW",
    )
    _require(
        qualified_at.utcoffset() is not None
        and _instant(rule["acquired_at"]) <= qualified_at
        and row["knowledge_from"] is not None
        and row["knowledge_from"] <= qualified_at,
        "SCHEDULE_NOT_AVAILABLE",
    )
    for field in ("observation_start", "observation_end"):
        _require(row[field].utcoffset() is not None, "SCHEDULE_AWARE_TIME_REQUIRED")
    _require(
        row["observation_start"].astimezone(TZ).date() == day
        and row["observation_start"] < row["observation_end"],
        "SCHEDULE_CALENDAR_DATE",
    )
    _require(
        bool(row["calendar_session_id"])
        and bool(row["venue_id"])
        and re.fullmatch(r"sha256:[0-9a-f]{64}", calendar_sha256) is not None,
        "SCHEDULE_SOURCE_IDENTITY",
    )
    return {
        "contract": CONTRACT,
        "calendar_session_id": row["calendar_session_id"],
        "venue_id": row["venue_id"],
        "mic": mic,
        "trade_date": day.isoformat(),
        "declared_regular_open_at": datetime.combine(day, time(9, 30), TZ).isoformat(),
        "declared_regular_close_at": datetime.combine(day, time(15), TZ).isoformat(),
        "native_open_at": None,
        "native_close_at": None,
        "calendar_member_sha256": calendar_sha256,
        "native_source_artifact_sha256": row["source_artifact_sha256"],
        "native_knowledge_from": row["knowledge_from"].isoformat(),
        "rule": dict(rule),
        "qualified_at": qualified_at.astimezone(timezone.utc).isoformat(),
        "evidence_kind": "rule_derived_not_source_observed",
        "historical_pit_granted": False,
        "consumer_admission_granted": False,
        "opening_auction_fill_proven": False,
    }


def schedule_rows(
    calendar: Path, venues: Path, rules: Mapping[str, Any], qualified_at: datetime
) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    mapping: dict[str, str] = {}
    for v in pq.read_table(venues).to_pylist():
        if v["mic_code"] not in RULES:
            continue
        _require(v["venue_id"] not in mapping, "SCHEDULE_AMBIGUOUS_VENUE")
        mapping[v["venue_id"]] = v["mic_code"]
    rows = []
    keys: set[str] = set()
    digest = sha(calendar)
    for c in pq.read_table(calendar).to_pylist():
        mic = mapping.get(c["venue_id"])
        if mic is None or c["is_open"] is not True:
            continue
        if c["open_at"] is not None and c["close_at"] is not None:
            continue
        _require(c["calendar_session_id"] not in keys, "SCHEDULE_DUPLICATE_SESSION")
        keys.add(c["calendar_session_id"])
        rows.append(
            derive_session(
                c, mic=mic, rule=rules[mic], calendar_sha256=digest, qualified_at=qualified_at
            )
        )
    return sorted(rows, key=lambda r: (r["trade_date"], r["mic"], r["calendar_session_id"]))


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def build(
    *,
    calendar: Path,
    venues: Path,
    source_hashes: Mapping[str, str],
    authority: Path,
    output: Path,
    qualified_at: datetime,
) -> str:
    """Small standalone metadata product; immutable output and READY last."""
    import shutil

    _require(not output.exists() and not output.is_symlink(), "SCHEDULE_OUTPUT_EXISTS")
    for source in (calendar, venues, authority):
        _require(not source.is_symlink(), "SCHEDULE_SOURCE_LINK")
        _require(
            not output.resolve().is_relative_to(source.resolve())
            and not source.resolve().is_relative_to(output.resolve()),
            "SCHEDULE_OVERLAP",
        )
    _require(
        sha(calendar) == source_hashes["calendar"] and sha(venues) == source_hashes["venues"],
        "SCHEDULE_INPUT_HASH",
    )
    rules = qualify_rules(authority, qualified_at)
    rows = schedule_rows(calendar, venues, rules, qualified_at)
    _require(bool(rows), "SCHEDULE_EMPTY")
    output.mkdir(parents=True)
    try:
        shutil.copyfile(calendar, output / "calendar.parquet")
        shutil.copyfile(venues, output / "venues.parquet")
        for spec in RULES.values():
            for key in ("body", "receipt"):
                shutil.copyfile(authority / spec[key], output / spec[key])
        (output / "declared-sessions.json").write_bytes(_canonical(rows))
        (output / "plan.json").write_bytes(
            _canonical(
                {
                    "contract": CONTRACT,
                    "source_hashes": dict(source_hashes),
                    "qualified_at": qualified_at.isoformat(),
                    "evidence_kind": "rule_derived_not_source_observed",
                    "native_values_mutated": False,
                    "historical_pit_granted": False,
                }
            )
        )
        manifest = {
            "contract": CONTRACT,
            "files": {
                p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
                for p in sorted(output.iterdir())
            },
        }
        (output / "manifest.json").write_bytes(_canonical(manifest))
        identity = sha(output / "manifest.json")
        verify(output, identity, ready=False)
        (output / "READY").write_text(identity + "\n")
        return identity
    except BaseException:
        # Keep incomplete output as failure evidence. Never seal it or overwrite a prior run.
        raise


def verify(root: Path, manifest_sha256: str, *, ready: bool = True) -> dict[str, Any]:
    _require(not root.is_symlink(), "SCHEDULE_SOURCE_LINK")
    expected = {"calendar.parquet", "venues.parquet", "declared-sessions.json", "plan.json"}
    expected |= {s[k] for s in RULES.values() for k in ("body", "receipt")}
    physical = expected | {"manifest.json"} | ({"READY"} if ready else set())
    _require({p.name for p in root.iterdir()} == physical, "SCHEDULE_FILE_SET")
    _require(
        all(p.is_file() and not p.is_symlink() for p in root.iterdir()), "SCHEDULE_SOURCE_LINK"
    )
    _require(sha(root / "manifest.json") == manifest_sha256, "SCHEDULE_MANIFEST_HASH")
    if ready:
        _require((root / "READY").read_text().strip() == manifest_sha256, "SCHEDULE_READY")
    m = json.loads((root / "manifest.json").read_bytes())
    _require(m["contract"] == CONTRACT and set(m["files"]) == expected, "SCHEDULE_MANIFEST")
    for n, v in m["files"].items():
        _require(
            sha(root / n) == v["sha256"] and (root / n).stat().st_size == v["bytes"],
            "SCHEDULE_MEMBER_HASH",
        )
    p = json.loads((root / "plan.json").read_bytes())
    _require(
        p["contract"] == CONTRACT
        and p["native_values_mutated"] is False
        and p["historical_pit_granted"] is False
        and p["evidence_kind"] == "rule_derived_not_source_observed",
        "SCHEDULE_CLAIM",
    )
    _require(
        sha(root / "calendar.parquet") == p["source_hashes"]["calendar"]
        and sha(root / "venues.parquet") == p["source_hashes"]["venues"],
        "SCHEDULE_INPUT_HASH",
    )
    at = _instant(p["qualified_at"])
    rules = qualify_rules(root, at)
    rows = schedule_rows(root / "calendar.parquet", root / "venues.parquet", rules, at)
    _require(
        bool(rows) and (root / "declared-sessions.json").read_bytes() == _canonical(rows),
        "SCHEDULE_RECOMPUTED_SEMANTICS",
    )
    return {
        "status": "DECLARED_SCHEDULE_QUALIFIED",
        "sessions": len(rows),
        "manifest_sha256": manifest_sha256,
        "native_source_clocks_repaired": False,
        "historical_pit_granted": False,
        "consumer_admission_granted": False,
    }
