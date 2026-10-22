"""Bounded P10 successor evidence; never invent an original IPO rule or stock event.

The retained SSE 2026 rule can establish that an already observed listing is
outside its current initial window. It does not reconstruct the rule on a
security's historical IPO date. Other venues and unresolved events remain held.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .base22 import digest
from .base24_status_evidence import _instant

CONTRACT = "p10-current-rule-window-exclusion-and-instrument-correction.v1"
SSE_RULE_SHA = "sha256:fc922c433438b2636cb631eab25cca405209712acbb6aaded768c45456ff8888"
SSE_RECEIPT_SHA = "sha256:26eb37cf08d85b140ab1d9666f8c1474840ec50cafe985debe17222992906310"
SSE_URI = (
    "https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/"
    "10816492/files/58bd5ce4dfc94b57a5391f60eb2719ab.docx"
)
EFFECTIVE = date(2026, 7, 6)


class RepairError(ValueError):
    """The bounded successor lacks exact evidence."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise RepairError(reason)


@dataclass(frozen=True)
class SseRule:
    source_sha256: str
    known: datetime


def load_sse_rule(body: Path, receipt: Path) -> SseRule:
    """Pin the existing complete official response and its real retrieval clock."""
    _require(
        "sha256:" + hashlib.sha256(body.read_bytes()).hexdigest() == SSE_RULE_SHA,
        "OFFICIAL_RULE_HASH",
    )
    _require(
        "sha256:" + hashlib.sha256(receipt.read_bytes()).hexdigest() == SSE_RECEIPT_SHA,
        "OFFICIAL_RECEIPT_HASH",
    )
    r = json.loads(receipt.read_bytes())
    _require(
        r["URI"] == SSE_URI
        and r["body_sha256"] == SSE_RULE_SHA
        and r["http_status"] == "200"
        and r["curl_exit"] == 0,
        "OFFICIAL_RECEIPT",
    )
    with zipfile.ZipFile(body) as archive:
        tree = ElementTree.fromstring(archive.read("word/document.xml"))
    text = re.sub(r"\s+", "", "".join(tree.itertext()))
    for clause in (
        "3.3.13本所对股票、基金交易实行价格涨跌幅限制，涨跌幅限制比例为10%。",
        "（一）首次公开发行上市的股票上市后的前5个交易日；",
        "（二）进入退市整理期交易的退市整理股票首个交易日；",
        "（三）退市后重新上市的股票首个交易日；",
        "11.9本规则自2026年7月6日起施行。",
    ):
        _require(clause in text, "OFFICIAL_CLAUSE")
    return SseRule(SSE_RULE_SHA, _instant(r["completed_at"]))


def _rule_visible(rule: SseRule, board: str, day: date, cutoff: datetime) -> None:
    _require(
        rule.source_sha256 == SSE_RULE_SHA
        and rule.known.utcoffset() is not None
        and cutoff.utcoffset() is not None
        and rule.known <= cutoff,
        "CURRENT_RULE_UNAVAILABLE",
    )
    _require(board == "SSE_main" and day >= EFFECTIVE, "CURRENT_RULE_SCOPE")


def exclude_ipo_window(
    rule: SseRule,
    listing: dict[str, Any],
    sessions: list[dict[str, Any]],
    target: date,
    cutoff: datetime,
) -> dict[str, Any]:
    """Six distinct actual sessions in one listing prove exit, not IPO history.

    All inputs must be from the frozen source/member closure. Calendar openness
    or listing age alone never counts. Source clocks are preserved, and no
    reconstruction of an initial or relisting session is attempted.
    """
    _rule_visible(rule, listing["board"], target, cutoff)
    _require(
        listing["venue"] == "XSHG"
        and listing["versions"] == 1
        and listing["knowledge_from"] is not None
        and listing["knowledge_from"].utcoffset() is not None
        and listing["knowledge_from"] <= cutoff,
        "LISTING_IDENTITY",
    )
    _require(
        listing["effective_from"] <= target
        and (listing["effective_to"] is None or target < listing["effective_to"]),
        "LISTING_SCOPE",
    )
    _require(len(sessions) >= 6, "INSUFFICIENT_OBSERVED_SESSIONS")
    days = set()
    for s in sessions:
        _require(
            s["listing_id"] == listing["listing_id"]
            and s["code"] == listing["code"]
            and s["venue"] == listing["venue"]
            and s["listing_versions"] == 1
            and s["calendar_versions"] == 1,
            "SESSION_IDENTITY",
        )
        _require(
            s["is_open"] is True
            and s["trading"] is True
            and listing["effective_from"] <= s["day"] <= target
            and s["knowledge_from"] is not None
            and s["knowledge_from"].utcoffset() is not None
            and s["knowledge_from"] <= cutoff,
            "SESSION_OBSERVATION",
        )
        _require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", s["source_sha256"]) is not None, "SESSION_SOURCE"
        )
        _require(s["day"] not in days, "DUPLICATE_SESSION")
        days.add(s["day"])
    proof = dict(
        contract_id=CONTRACT,
        code=listing["code"],
        listing_id=listing["listing_id"],
        board=listing["board"],
        market_date=target,
        rule_sha256=rule.source_sha256,
        rule_known_at=rule.known,
        knowledge_cutoff=cutoff,
        observed_session_count=len(days),
        listing=listing,
        sessions=sorted(sessions, key=lambda s: s["day"]),
        in_ipo_window=False,
        original_ipo_rule_recovered=False,
        historical_pit_granted=False,
        consumer_admission_granted=False,
    )
    return {**proof, "identity": digest(proof)}


def validate_window_proof(proof: dict[str, Any], row: dict[str, Any]) -> None:
    """Recompute the proof and bind it to this exact native observation."""
    rebuilt = exclude_ipo_window(
        SseRule(proof["rule_sha256"], proof["rule_known_at"]),
        proof["listing"],
        proof["sessions"],
        proof["market_date"],
        proof["knowledge_cutoff"],
    )
    _require(proof == rebuilt, "WINDOW_PROOF_SEMANTICS")
    _require(
        (proof["listing_id"], proof["code"], proof["market_date"], proof["board"])
        == (row["listing_id"], row["listing_code"], row["market_date"], row["board"]),
        "WINDOW_PROOF_TARGET",
    )
    _require(
        any(x["kind"] == "LISTING_LIFECYCLE" and x["value"] == "LISTED" for x in row["lifecycle"]),
        "WINDOW_LIFECYCLE",
    )
    _require(
        not any(
            x["value"] in {"DELISTING", "DELISTED", "RELISTED", "SUSPENDED"}
            for x in row["lifecycle"]
        ),
        "SPECIAL_LIFECYCLE_UNRESOLVED",
    )


def event_instrument(title: str) -> str:
    """Only an explicit exclusive bond subject can withdraw a stock claim.

    Revoke-star titles, intentions, mixed stock/bond notices and convertible
    instruments remain unresolved. No NORMAL stock state is inferred here.
    """
    if re.fullmatch(r"关于[0-9]{4}年公司债券(?:（[^（）]+）)?(?:停牌暨)?实行风险警示的公告", title):
        return "BOND_ONLY"
    return "UNRESOLVED"


def withdraw_bond_claim(
    native: dict[str, Any],
    raw: dict[str, Any],
    event: dict[str, Any],
    *,
    raw_sha256: str,
    event_sha256: str,
    repair_at: datetime,
) -> dict[str, Any]:
    """A new UNKNOWN stock assertion; old values and original times stay in proof."""
    from urllib.parse import parse_qs, urlparse

    _require(repair_at.utcoffset() is not None, "REPAIR_CLOCK")
    _require(event_instrument(event["title"]) == "BOND_ONLY", "EVENT_INSTRUMENT_UNRESOLVED")
    query = parse_qs(urlparse(raw["source_url_or_notice_id"]).query)
    _require(
        query.get("announcementId") == [event["ann_id"]]
        and query.get("stockCode") == [raw["stock_code"]]
        and raw["stock_code"] == event["stock_code"]
        and raw["event_kind"] == event["event_kind"] == "implement_other"
        and raw["announcement_date"] == event["announcement_date"],
        "EVENT_SOURCE_LINK",
    )
    _require(
        native["status_type"] == "SPECIAL_TREATMENT"
        and native["status_value"] == "ST"
        and native["source_artifact_sha256"] == raw_sha256
        and native["source_record_id"]
        == "payload/canonical/ref_st_status_pit.parquet#interval_id=" + raw["interval_id"],
        "NATIVE_SOURCE_LINK",
    )
    _require(
        all(re.fullmatch(r"sha256:[0-9a-f]{64}", s) for s in (raw_sha256, event_sha256)),
        "SOURCE_IDENTITY",
    )
    proof = dict(
        contract_id=CONTRACT,
        native=native,
        raw=raw,
        event=event,
        raw_sha256=raw_sha256,
        event_sha256=event_sha256,
        repair_known_at=repair_at.astimezone(timezone.utc),
        reason="BOND_NOTICE_MISAPPLIED_TO_EQUITY",
        successor_value="UNKNOWN",
        original_source_unchanged=True,
        historical_pit_granted=False,
        consumer_admission_granted=False,
    )
    return {**proof, "identity": digest(proof)}


def sse_st_successor(
    rule: SseRule,
    native: dict[str, Any],
    *,
    mic: str,
    repair_at: datetime,
) -> dict[str, Any]:
    """Version the stale SSE ST overlay; retain its earlier valid interval.

    This is a new source revision observed now, not a mutation of the 1998
    record, an inference of the publication clock, or a SZSE rule assertion.
    """
    _rule_visible(rule, "SSE_main" if mic == "XSHG" else "OTHER", EFFECTIVE, repair_at)
    _require(
        native["security_status"] == "ST"
        and native["board_code"] is None
        and native["listing_id"] is None
        and native["upper_limit_ratio"] == native["lower_limit_ratio"] == Decimal("0.05")
        and native["effective_from"].date() < EFFECTIVE
        and native["effective_to"] is None,
        "OLD_ST_REGIME_SCOPE",
    )
    boundary = datetime(2026, 7, 6, tzinfo=ZoneInfo("Asia/Shanghai"))
    provenance = dict(
        record_source=CONTRACT,
        source_revision=CONTRACT,
        source_record_id=SSE_URI + "#3.3.13+4.4.1+11.9",
        source_artifact_sha256=rule.source_sha256,
        knowledge_from=repair_at,
        knowledge_to=None,
    )
    prior = {**native, **provenance, "effective_to": boundary}
    replacement = {
        **native,
        **provenance,
        "price_limit_regime_id": "price_limit_regime_"
        + digest([native["price_limit_regime_id"], rule.source_sha256, EFFECTIVE])[7:39],
        "effective_from": boundary,
        "effective_to": None,
        "upper_limit_ratio": Decimal("0.1"),
        "lower_limit_ratio": Decimal("0.1"),
    }
    proof = dict(
        contract_id=CONTRACT,
        original=native,
        retained_historical_fragment=prior,
        replacement=replacement,
        rule_sha256=rule.source_sha256,
        rule_known_at=rule.known,
        repair_known_at=repair_at,
        historical_pit_granted=False,
        consumer_admission_granted=False,
    )
    return {**proof, "identity": digest(proof)}


def replay_package(root: Path) -> dict[str, Any]:
    """Recompute this small evidence package using only its complete local files."""
    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from quanthus.backtest.base24_semantic_projection import NATIVE_KEYS

    from .base22 import file_sha
    from .base24_status_evidence import raw_observations, select_status

    spec = json.loads((root / "INPUTS.json").read_bytes())
    for name, sha in spec["files"].items():
        _require(Path(name).name == name and file_sha(root / name) == sha, "LOCAL_INPUT_HASH")
    ods = json.loads((root / "ods-manifest.json").read_bytes())["content"]["files"]
    native = json.loads((root / "native-manifest.json").read_bytes())["files"]
    for entry in spec["ods_members"]:
        _require(
            entry in ods
            and file_sha(root / ("raw-" + Path(entry["path"]).name)) == entry["sha256"],
            "ODS_SOURCE_MEMBERSHIP",
        )
    for table, entry in spec["native_members"].items():
        _require(
            entry == native["native/" + table + ".parquet"]
            and file_sha(root / (table + ".parquet")) == entry["sha256"],
            "NATIVE_SOURCE_MEMBERSHIP",
        )
    _require(file_sha(root / "price-limit.v2.json") == spec["policy_sha256"], "FROZEN_POLICY")
    cutoff = _instant(spec["snapshot_cutoff"])
    repair_at = _instant(spec["repair_known_at"])
    _require(repair_at >= cutoff, "REPAIR_CLOCK")
    rule = load_sse_rule(root / "sse-rule-docx.raw", root / "sse-rule-docx.receipt.json")
    c = duckdb.connect()
    c.execute("SET memory_limit='1GB'")
    c.execute("SET threads=1")
    c.execute("SET TimeZone='Asia/Shanghai'")
    keys = dict(NATIVE_KEYS, dwd_security_status_interval="security_status_id,effective_from")
    for table in spec["native_members"]:
        c.execute(
            f"CREATE TABLE {table}_raw AS SELECT * FROM read_parquet(?)",
            [str(root / (table + ".parquet"))],
        )
        c.execute(
            f"""CREATE VIEW {table} AS SELECT *,count(*) OVER(PARTITION BY {keys[table]})
          versions FROM (SELECT DISTINCT * FROM {table}_raw
          WHERE knowledge_from<=TIMESTAMPTZ '{cutoff.isoformat()}'
          AND coalesce(knowledge_to,'infinity'::TIMESTAMPTZ)>TIMESTAMPTZ '{cutoff.isoformat()}')
          QUALIFY row_number() OVER(PARTITION BY {keys[table]} ORDER BY knowledge_from DESC)=1""",
            [],
        )

    def rows(sql: str, args: list[Any]) -> list[dict[str, Any]]:
        cur = c.execute(sql, args)
        columns = [x[0] for x in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

    windows = []
    for request in spec["ipo_requests"]:
        code, day = request["code"], date.fromisoformat(request["day"])
        ls = rows(
            """SELECT l.*,v.mic_code,v.versions venue_versions FROM dwd_listing l
          JOIN dwd_trading_venue v USING(venue_id) WHERE l.listing_code=?
          AND CAST(l.effective_from AS DATE)<=?
          AND coalesce(CAST(l.effective_to AS DATE),DATE '9999-12-31')>?
          AND CAST(v.effective_from AS DATE)<=?
          AND coalesce(CAST(v.effective_to AS DATE),DATE '9999-12-31')>?""",
            [code, day, day, day, day],
        )
        _require(len(ls) == 1 and ls[0]["venue_versions"] == 1, "LISTING_IDENTITY")
        native_listing = ls[0]
        listing = dict(
            listing_id=native_listing["listing_id"],
            code=code,
            board=native_listing["board_code"],
            venue=native_listing["mic_code"],
            versions=native_listing["versions"],
            knowledge_from=native_listing["knowledge_from"],
            effective_from=native_listing["effective_from"].date(),
            effective_to=native_listing["effective_to"].date()
            if native_listing["effective_to"]
            else None,
        )
        raw_path = root / ("raw-" + code + ".json")
        raw = json.loads(raw_path.read_bytes())
        pairs = {
            (code, r["date"]) for r in raw["rows"] if "2026-01-01" <= r["date"] <= day.isoformat()
        }
        raw_sha = spec["files"][raw_path.name]
        obs = raw_observations(raw, pairs, raw_sha)
        selected = select_status(
            [dict(stock_code=k, market_date=d) for k, d in sorted(pairs)],
            obs,
            spec["snapshot_cutoff"],
        )
        sessions = []
        for observation in reversed(selected):
            d = date.fromisoformat(observation["market_date"])
            if not observation["qualified"] or observation["reason"] != "PROVIDER_TRADING":
                continue
            cal = rows(
                "SELECT * FROM dwd_trading_calendar WHERE venue_id=? AND trade_date=?",
                [native_listing["venue_id"], d],
            )
            if len(cal) != 1 or cal[0]["versions"] != 1 or not cal[0]["is_open"]:
                continue
            same = rows(
                "SELECT * FROM dwd_listing WHERE listing_id=? AND CAST(effective_from AS DATE)<=? "
                "AND coalesce(CAST(effective_to AS DATE),DATE '9999-12-31')>?",
                [native_listing["listing_id"], d, d],
            )
            if len(same) != 1 or same[0]["versions"] != 1:
                continue
            sessions.append(
                dict(
                    day=d,
                    listing_id=native_listing["listing_id"],
                    code=code,
                    venue=native_listing["mic_code"],
                    calendar_versions=1,
                    listing_versions=1,
                    is_open=True,
                    trading=True,
                    source_sha256=raw_sha,
                    knowledge_from=_instant(observation["knowledge_from"]),
                )
            )
            if len(sessions) == 6:
                break
        windows.append(exclude_ipo_window(rule, listing, sessions, day, cutoff))

    raw_path = root / "raw-ref_st_status_pit.parquet"
    event_path = root / "raw-events_audit.parquet"
    raw_rows = pq.read_table(raw_path).to_pylist()
    events = pq.read_table(event_path).to_pylist()
    withdrawals = {}
    classifications = []
    for request in spec["status_requests"]:
        code, day = request["code"], date.fromisoformat(request["day"])
        claims = rows(
            """SELECT x.* FROM dwd_security_status_interval x JOIN dwd_listing l
          USING(security_id) WHERE l.listing_code=? AND x.status_type='SPECIAL_TREATMENT'
          AND CAST(x.effective_from AS DATE)<=?
          AND coalesce(CAST(x.effective_to AS DATE),DATE '9999-12-31')>?""",
            [code, day, day],
        )
        for claim in claims:
            matches = [
                r
                for r in raw_rows
                if r["stock_code"] == code
                and claim["source_record_id"].endswith("#interval_id=" + r["interval_id"])
            ]
            _require(len(matches) == 1, "RAW_STATUS_LINK")
            raw_row = matches[0]
            matched = [
                e
                for e in events
                if e["stock_code"] == code and e["url"] == raw_row["source_url_or_notice_id"]
            ]
            _require(len(matched) == 1, "EVENT_STATUS_LINK")
            event = matched[0]
            kind = event_instrument(event["title"])
            classifications.append(
                dict(
                    code=code,
                    day=day,
                    title=event["title"],
                    event_id=event["ann_id"],
                    classification=kind,
                )
            )
            if kind == "BOND_ONLY":
                proof = withdraw_bond_claim(
                    claim,
                    raw_row,
                    event,
                    raw_sha256=spec["files"][raw_path.name],
                    event_sha256=spec["files"][event_path.name],
                    repair_at=repair_at,
                )
                withdrawals[claim["security_status_id"]] = proof
    regimes = rows(
        """SELECT r.* EXCLUDE(versions) FROM dwd_price_limit_regime r
      JOIN dwd_trading_venue v USING(venue_id) WHERE v.mic_code='XSHG'
      AND r.security_status='ST' AND r.versions=1 AND v.versions=1""",
        [],
    )
    _require(len(regimes) == 1, "ST_REGIME_IDENTITY")
    st_revision = sse_st_successor(rule, regimes[0], mic="XSHG", repair_at=repair_at)
    c.close()
    return dict(
        contract_id=CONTRACT,
        snapshot_cutoff=cutoff,
        repair_known_at=repair_at,
        ipo_exclusions=windows,
        bond_claim_withdrawals=list(withdrawals.values()),
        event_classifications=classifications,
        sse_st_revision=st_revision,
        consumer_admission_granted=False,
        historical_pit_granted=False,
    )


def seal_evidence(root: Path, code_revision: str) -> str:
    """Publish once after local semantic reconstruction; keep failed work."""
    from .base22 import canonical, file_sha
    from .base22_sources import _tree, physical_path

    root = physical_path(root)
    spec = json.loads((root / "INPUTS.json").read_bytes())
    _require(set(_tree(root)) == set(spec["files"]) | {"INPUTS.json"}, "UNSEALED_FILE_SET")
    _require(re.fullmatch(r"[0-9a-f]{40}", code_revision) is not None, "CODE_REVISION")
    result = replay_package(root)
    (root / "OUTPUTS.json").write_bytes(canonical(result))
    engine = dict(
        contract_id=CONTRACT,
        code_revision=code_revision,
        module_sha256=file_sha(Path(__file__)),
        consumer_admission_granted=False,
    )
    (root / "ENGINE.json").write_bytes(canonical(engine))
    manifest = dict(
        contract_id=CONTRACT,
        files={p: file_sha(root / p) for p in sorted(_tree(root))},
        built_at=datetime.now(timezone.utc).isoformat(),
        consumer_admission_granted=False,
    )
    (root / "manifest.json").write_bytes(canonical(manifest))
    sha = file_sha(root / "manifest.json")
    verify_evidence(root, sha, ready=False)
    (root / "READY").write_text(sha + "\n", encoding="utf-8")
    return sha


def verify_evidence(root: Path, manifest_sha256: str, *, ready: bool = True) -> dict[str, Any]:
    from .base22 import canonical, file_sha
    from .base22_sources import _tree, physical_path

    root = physical_path(root)
    _require(file_sha(root / "manifest.json") == manifest_sha256, "MANIFEST_HASH")
    manifest = json.loads((root / "manifest.json").read_bytes())
    _require(
        manifest["contract_id"] == CONTRACT and manifest["consumer_admission_granted"] is False,
        "PURPOSE_CONTRACT",
    )
    expected = set(manifest["files"]) | {"manifest.json"} | ({"READY"} if ready else set())
    _require(set(_tree(root)) == expected, "PHYSICAL_FILE_SET")
    for name, sha in manifest["files"].items():
        _require(file_sha(root / name) == sha, "FILE_HASH")
    if ready:
        _require((root / "READY").read_text().strip() == manifest_sha256, "READY_HASH")
    engine = json.loads((root / "ENGINE.json").read_bytes())
    _require(
        engine["module_sha256"] == file_sha(Path(__file__))
        and engine["contract_id"] == CONTRACT
        and engine["consumer_admission_granted"] is False,
        "ENGINE_IDENTITY",
    )
    spec = json.loads((root / "INPUTS.json").read_bytes())
    _require(
        set(manifest["files"])
        == set(spec["files"]) | {"INPUTS.json", "OUTPUTS.json", "ENGINE.json"},
        "SEMANTIC_FILE_SET",
    )
    result = replay_package(root)
    _require((root / "OUTPUTS.json").read_bytes() == canonical(result), "RECOMPUTED_SEMANTICS")
    return dict(
        manifest_sha256=manifest_sha256,
        ipo_exclusions=len(result["ipo_exclusions"]),
        withdrawn_bond_claims=len(result["bond_claim_withdrawals"]),
        historical_pit_granted=False,
        consumer_admission_granted=False,
    )
