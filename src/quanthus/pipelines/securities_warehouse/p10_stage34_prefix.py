"""Bounded P10 intake of an exactly selected, existing 2021 native prefix.

This module owns source correspondence, not Stage references or model quality.
Even a non-action census pass cannot admit a prefix whose action coverage is unknown.
No source lookup, network acquisition, repair, price adjustment or production switch exists here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
FIELDS = ("open", "high", "low", "close", "volume")
VERSION = "p10-stage34-prefix128-source-v1"


class PrefixSourceError(ValueError):
    """Source failures use safe reason codes, never numeric market values."""


def checked_member(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    path = root / relative
    if (not relative or parts.is_absolute() or ".." in parts.parts
            or root.resolve() != root or path.resolve() != path
            or not path.is_file() or not path.is_relative_to(root)):
        raise PrefixSourceError("MEMBER_PATH")
    return path


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            h.update(block)
    return h.hexdigest()


def observe_f10_action_dates(
    row: Mapping[str, Any], start: date, end: date,
) -> dict[str, Any]:
    """Extract date observations, retaining unresolved scope and numbered sections.

    Amount parsing is a different consumer's contract. Neither an unsupported
    amount nor a pre-IPO fiscal coordinate may invent or suppress an ex-date.
    Complete official inventory/body qualification remains a separate gate.
    """
    content = row.get("content")
    code = row.get("stock_code")
    if (not isinstance(content, str) or len(content.encode()) > 1024**2
            or not isinstance(code, str) or re.fullmatch(r"\d{6}", code) is None
            or row.get("security_id") != code or row.get("category") != "分红扩股"
            or hashlib.sha256(content.encode()).hexdigest()
            != str(row.get("content_sha256")).removeprefix("sha256:")
            or code not in content.splitlines()[0]
            or not date(2021, 1, 1) <= start <= end <= date(2021, 12, 31)):
        raise PrefixSourceError("F10_DOCUMENT_IDENTITY")
    headers = list(re.finditer(r"^\s*【([^】]+)】", content, re.M))
    sections = {}
    for i, match in enumerate(headers):
        name = re.sub(r"^\d+[.、]", "", match.group(1)).strip()
        if name in sections:
            raise PrefixSourceError("F10_DUPLICATE_SECTION")
        sections[name] = content[match.end():headers[i+1].start() if i+1 < len(headers)
                                 else len(content)]
    if "分红" not in sections:
        raise PrefixSourceError("F10_DIVIDEND_SECTION")
    blocks: list[tuple[str, list[str]]] = []
    coordinate = None
    lines: list[str] = []
    for line in sections["分红"].splitlines():
        cells = [cell.strip() for cell in line.split("｜")[1:-1]]
        if not cells:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            if coordinate is not None:
                blocks.append((coordinate, lines))
            coordinate, lines = cells[0], []
        if coordinate is not None:
            lines.append(cells[1] if len(cells) > 1 else "")
    if coordinate is not None:
        blocks.append((coordinate, lines))
    if len(blocks) > 512:
        raise PrefixSourceError("F10_BLOCK_BOUND")
    output = []
    observed = set()
    for ordinal, (coordinate, lines) in enumerate(blocks):
        text = "\n".join(lines).replace(" ", "").replace("\u3000", "")
        progress = set(re.findall(r"进展说明[:：]([^\n]+)", text))
        dates = sorted(set(re.findall(r"除权除息日[:：](\d{4}-\d{2}-\d{2})", text)))
        disposition = "PROGRESS_UNKNOWN"
        if progress in ({"决案"}, {"预案"}):
            disposition = "PROPOSAL_DECISION_NOT_IMPLEMENTED"
        elif progress == {"实施"}:
            disposition = "IMPLEMENTED_EX_DATE_UNKNOWN"
            if len(dates) > 1:
                disposition = "EX_DATE_CONFLICT"
            elif len(dates) == 1:
                day = date.fromisoformat(dates[0])
                disposition = "OBSERVED_EX_DATE" if start <= day <= end else "DATED_OUTSIDE_PREFIX"
                if disposition == "OBSERVED_EX_DATE":
                    observed.add(dates[0])
        output.append({"ordinal": ordinal, "fiscal_coordinate": coordinate,
                       "block_sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
                       "ex_date_observations": dates, "disposition": disposition})
    rights = sections.get("配股")
    return {
        "kind": "p10-f10-coordinate-date-observations-v1", "code": code,
        "content_sha256": row["content_sha256"], "provider_acquired_at": row.get("acquired_at"),
        "observed_ex_dates": sorted(observed), "blocks": output,
        "unknown_date_blocks": sum(r["disposition"] in {
            "IMPLEMENTED_EX_DATE_UNKNOWN", "EX_DATE_CONFLICT", "PROGRESS_UNKNOWN"
        } for r in output),
        "rights_status": "EXPLICIT_PROVIDER_EMPTY" if rights is not None and re.fullmatch(
            r"\s*暂无数据\s*", rights
        ) else "UNRESOLVED_PROVIDER_SECTION",
        "action_coverage_qualified": False, "financial_amounts_qualified": False,
    }


def official_ex_date(text: str, code: str, exchange: str) -> dict[str, Any]:
    """Require literal, unambiguous ex-date evidence in an issuer-bound body.

    The caller verifies publisher metadata, PDF and text hashes. Quotes retain
    character and UTF-8 offsets. Dates do not qualify amounts or old knowledge.
    """
    compact = re.sub(r"\s+", "", text[:2000])
    if (exchange not in {"SSE", "SZSE"} or len(text.encode()) > 4 * 2**20
            or re.fullmatch(r"\d{6}", code) is None
            or re.search(r"(?:证券|股票)代码[:：]?" + code, compact) is None):
        raise PrefixSourceError("OFFICIAL_CODE_IDENTITY")
    observations: list[tuple[date, str, str | None]] = []
    label = (r"(?:除\s*权(?:\s*除\s*息(?:\s*息)?|\s*[（(]\s*息\s*[）)])?"
             r"|除\s*息)\s*日")
    for match in re.finditer(
        label + r"(?:[、，]红利发放日)?[\s为：:]*(20\d{2})\s*年\s*"
        r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text,
    ):
        observations.append((date(*map(int, match.groups())), match.group(), None))
    if exchange == "SSE":
        lines = text.splitlines(keepends=True)

        for i, line in enumerate(lines):
            header_lines = lines[max(0, i-6):i]
            header = "".join(header_lines)
            row = re.fullmatch(r"\s*(?:[AＡ]\s*股\s+)?([0-9/－—–\s-]+)", line)
            if not row or "股份类别" in header and not re.match(r"\s*[AＡ]\s*股", line):
                continue
            records = [m.start() for h in header_lines
                       for m in re.finditer("股权登记日", h)]
            ex_columns = [m.start() for h in header_lines
                          for m in re.finditer(r"除权(?:除息|[（(]息[）)])?|除息", h)]
            last_columns = [m.start() for h in header_lines
                            for m in re.finditer("最后交易", h)]
            if (not records or not ex_columns or max(records) >= min(ex_columns)
                    or last_columns and not max(records) < min(last_columns)
                    <= max(last_columns) < min(ex_columns)):
                continue
            # Preserve placeholder cells. The second *nonempty date* can be the
            # last-trading date, and the last date can be cash or new-share arrival.
            # pdftotext spaces do not reliably preserve CJK glyph-center alignment.
            cells = row[1].split()
            column = 2 if last_columns else 1
            if len(cells) <= column:
                continue
            cell = re.fullmatch(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", cells[column])
            if cell and all(re.fullmatch(r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}|[－—–-]+", c)
                            for c in cells):
                observations.append((date(*map(int, cell.groups())), line.strip(), header))
    if not observations:
        raise PrefixSourceError("OFFICIAL_EX_DATE_UNPROVEN")
    if len({item[0] for item in observations}) != 1:
        raise PrefixSourceError("OFFICIAL_EX_DATE_CONFLICT")

    def proof(fragment: str) -> dict[str, Any]:
        offset = text.index(fragment)
        return {"start": offset, "end": offset+len(fragment), "quote": fragment,
                "utf8_byte_start": len(text[:offset].encode()),
                "utf8_byte_end": len(text[:offset+len(fragment)].encode())}

    return {"ex_date": observations[0][0].isoformat(),
            "date_evidence": [proof(s) for s in dict.fromkeys(o[1] for o in observations)],
            "header_evidence": [proof(s) for s in dict.fromkeys(
                o[2] for o in observations if o[2]
            )], "historical_provider_acquisition": None,
            "amounts_or_execution_terms_admitted": False}


def payment_date_only_correction(
    correction: str, original: str, updated: str, code: str, exchange: str,
) -> dict[str, Any]:
    """Bind an explicit payment-date erratum without inventing an ex-date.

    Both complete implementations must independently state the same ex-date.
    Their separate publication/acquisition and cancellation observations remain
    the caller's responsibility; a cancelled body is never the operative source.
    """
    compact = [re.sub(r"\s+", "", s) for s in (correction, original, updated)]
    if (any(len(s.encode()) > 4 * 2**20 for s in (correction, original, updated))
            or re.fullmatch(r"\d{6}", code) is None
            or any(re.search(r"(?:证券|股票)代码[:：]?" + code, s[:2000]) is None
                   for s in compact)):
        raise PrefixSourceError("CORRECTION_ISSUER")
    c, o, u = compact
    number = re.search(r"公告编号[:：](\d{4}-\d+)", o[:2000])
    changes = re.search(r"更正前[:：](.*?)更正后[:：](.*?)除上述更正外[，,]?原公告其他内容不变", c)
    if (number is None or re.search(r"公告编号[:：]" + number[1], c) is None
            or changes is None or "权益分派实施公告（更正后）" not in u[:2000]):
        raise PrefixSourceError("CORRECTION_REVISION_CLOSURE")
    before, after = changes.groups()
    payment = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
    if (any(not s.startswith("五、权益分派方法") or "现金红利将于" not in s
            or "直接划入其资金账户" not in s or re.search(r"除权|除息|登记|配股|转增|送股", s)
            for s in (before, after))
            or len(payment.findall(before)) != 1 or len(payment.findall(after)) != 1
            or payment.sub("<PAYMENT_DATE>", before) != payment.sub("<PAYMENT_DATE>", after)):
        raise PrefixSourceError("CORRECTION_NOT_PAYMENT_DATE_ONLY")
    old_date, new_date = [date(*map(int, payment.findall(s)[0])).isoformat()
                          for s in (before, after)]
    old = official_ex_date(original, code, exchange)
    new = official_ex_date(updated, code, exchange)
    if old["ex_date"] != new["ex_date"]:
        raise PrefixSourceError("CORRECTION_EX_DATE_CONFLICT")
    return {"disposition": "PAYMENT_DATE_ONLY_CORRECTION_EX_DATE_UNCHANGED",
            "referenced_notice_number": number[1], "ex_date": new["ex_date"],
            "before_payment_date": old_date, "after_payment_date": new_date,
            "original_body_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "updated_body_sha256": hashlib.sha256(updated.encode()).hexdigest(),
            "correction_body_sha256": hashlib.sha256(correction.encode()).hexdigest(),
            "date_source": "UPDATED_IMPLEMENTATION_LITERAL_EX_DATE",
            "correction_itself_has_no_ex_date_proof": True,
            "amounts_or_execution_terms_admitted": False}


def nonimplementation_title_disposition(title: str) -> str:
    """Classify only a screened title's coordinate role, never its financial truth.

    Implementations/corrections require bodies. Unknown titles remain unresolved.
    Proposal/plan titles cannot suppress a separately observed implementation.
    """
    title = re.sub(r"\s+", "", title)
    if "实施" in title or "配股" in title:
        return "BODY_REQUIRED"
    if ("子公司" in title or "曾孙公司" in title) and any(
        s in title for s in ("借款转增为资本公积", "变更名称", "利润分配")
    ):
        return "SUBSIDIARY_NOT_LISTED_HOLDER_COORDINATE"
    if ("股东" in title and "变更名称" in title) or "控股股东改制更名" in title:
        return "SHAREHOLDER_NAME_NOT_LISTED_SECURITY_IDENTITY"
    if "首次公开发行" in title and "上市公告书" in title:
        return "IPO_DOCUMENT_IDENTITY_CHECKED_SEPARATELY"
    if "利润分配管理制度" in title:
        return "GOVERNANCE_NOT_IMPLEMENTATION"
    if any(s in title for s in ("利润分配", "拟不进行利润分配")):
        return "PROPOSAL_PLAN_OR_INVESTOR_MEETING_NOT_IMPLEMENTATION"
    return "UNRESOLVED_TITLE"


def _day(value: Any) -> date:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PrefixSourceError("OBSERVATION_CLOCK")
    return value.astimezone(SH).date()


def _unique(rows: Sequence[Mapping[str, Any]], key: Callable[[Any], Any]) -> dict[Any, Any]:
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise PrefixSourceError("DUPLICATE_KEY")
        result[identity] = row
    return result


def validate_selected_native(
    selected: Sequence[Mapping[str, Any]], tables: Mapping[str, Sequence[Mapping[str, Any]]],
    raw_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Census native rows against exact listing/date/venue and raw source identity.

    Missing bars and nontrading observations remain counts and fail closed. This
    function never translates missingness into structural damage or changes a state.
    """
    by_listing = _unique(selected, lambda s: s["listing_id"])
    if (not selected or len({s["security_id"] for s in selected}) != len(selected)
            or len({s["code"] for s in selected}) != len(selected)):
        raise PrefixSourceError("SELECTION_SCOPE")
    for s in selected:
        if not (date(2021, 1, 1) <= date.fromisoformat(s["first_listing"])
                == date.fromisoformat(s["proposed_start"])
                <= date.fromisoformat(s["proposed_through"]) <= date(2021, 12, 31)):
            raise PrefixSourceError("SELECTION_SCOPE")
    listings = _unique(tables["dwd_listing"], lambda r: r["listing_id"])
    if set(listings) != set(by_listing):
        raise PrefixSourceError("LISTING_SET")
    for key, s in by_listing.items():
        row = listings[key]
        if (any(row[k] != s[v] for k, v in [
                ("security_id", "security_id"), ("listing_code", "code"),
                ("venue_id", "venue_id")]) or row["listing_currency"] != "CNY"
                or _day(row["effective_from"]) != date.fromisoformat(s["first_listing"])):
            raise PrefixSourceError("LISTING_IDENTITY")
    calendar = _unique(tables["dwd_trading_calendar"], lambda r: r["calendar_session_id"])
    _unique(tables["dwd_trading_calendar"],
            lambda r: (r["venue_id"], r["trade_date"], r["session_type"]))
    expected = set()
    for lid, s in by_listing.items():
        for cal in calendar.values():
            day = cal["trade_date"]
            if (cal["venue_id"] == s["venue_id"] and cal["is_open"] is True
                    and cal["session_type"] == "REGULAR_DAY"
                    and date.fromisoformat(s["proposed_start"]) <= day
                    <= date.fromisoformat(s["proposed_through"])):
                expected.add((lid, day))
    observations = {}
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        rows = _unique(tables[table], lambda r: (r["listing_id"], _day(r["observation_start"])))
        for key, row in rows.items():
            selected_row = by_listing.get(key[0])
            if selected_row is None or key not in expected:
                raise PrefixSourceError("OBSERVATION_SCOPE")
            cal = calendar.get(row["calendar_session_id"])
            if (cal is None or cal["trade_date"] != key[1]
                    or cal["venue_id"] != selected_row["venue_id"]
                    or cal["is_open"] is not True):
                raise PrefixSourceError("CALENDAR_CLOSURE")
            if table == "dwd_security_bar" and (
                    row["interval_size"], row["price_basis"], row["currency"]
            ) != ("1d", "UNADJUSTED", "CNY"):
                raise PrefixSourceError("PRICE_COORDINATE")
        observations[table] = rows
    raws = {}
    for name in ("main", "hotfix"):
        digest = raw_hashes[name].removeprefix("sha256:")
        for row in tables["ods_" + name]:
            key = (digest, row["stock_code"], str(row["date"])[:10])
            if key in raws:
                raise PrefixSourceError("DUPLICATE_RAW_KEY")
            raws[key] = row
    exact: Counter[str] = Counter()
    null: Counter[str] = Counter()
    zero: Counter[str] = Counter()
    bars = observations["dwd_security_bar"]
    for (lid, day), bar in bars.items():
        source = bar["source_artifact_sha256"].removeprefix("sha256:")
        raw = raws.get((source, by_listing[lid]["code"], day.isoformat()))
        if raw is None:
            raise PrefixSourceError("RAW_LINEAGE")
        for field in FIELDS:
            value = None if raw[field] is None else Decimal(str(raw[field]))
            if value is not None and not value.is_finite():
                raise PrefixSourceError("NONFINITE_RAW")
            if value != bar[field]:
                raise PrefixSourceError("RAW_NUMERIC")
            exact[field] += 1
            if value is None:
                null[field] += 1
            elif value == 0:
                zero[field] += 1
    states = observations["dwd_tradability_state"]
    missing_bar = expected - bars.keys()
    missing_state = expected - states.keys()
    nontrading = sum(row["is_tradable"] is not True for row in states.values())
    limited = bool(missing_bar or missing_state or nontrading or null)
    return {
        "status": "DATA_LIMITED_NON_ACTION_CENSUS" if limited else "PASS_NON_ACTION_CLOSURE_ONLY",
        "selected_issuers": len(selected), "calendar_open_keys": len(expected),
        "native_daily_keys": len(bars), "state_keys": len(states),
        "missing_bar_keys": len(missing_bar), "missing_state_keys": len(missing_state),
        "nontrading_state_keys": nontrading, "exact_field_counts": dict(exact),
        "null_fields": dict(null), "zero_fields": dict(zero),
        "source_purpose_admitted": False, "action_coverage": "NOT_QUALIFIED",
        "price_values_exported": False,
    }


def run_existing_prefix(plan_path: Path, selection_path: Path) -> dict[str, Any]:
    """Verify once, extract once, then consume only that immutable job-local subset."""
    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    started = time.monotonic()
    plan = json.loads(plan_path.read_text())
    job = plan_path.parent
    output = Path(plan["output"])
    roots = [Path(plan["DWD_root"]), Path(plan["ODS_root"])]
    budget = plan["budget"]
    if (sha_file(selection_path) != plan["selection_sha256"]
            or sha_file(job / "successor-request.frozen.json") != plan["request_sha256"]
            or output.exists() or output.resolve() != output):
        raise PrefixSourceError("OUTPUT_OR_SELECTION")
    for root in roots:
        if (root.resolve() != root or output.is_relative_to(root)
                or root.is_relative_to(output) or job.is_relative_to(root)
                or root.is_relative_to(job)):
            raise PrefixSourceError("OUTPUT_SOURCE_OVERLAP")

    def save(name: str, value: Any) -> None:
        with (job / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")

    def guard() -> None:
        if (time.monotonic() - started > budget["existing_source_phase_seconds"]
                or datetime.now(timezone.utc) > datetime.fromisoformat(plan["deadline"])
                or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                > budget["rss_bytes"]):
            raise PrefixSourceError("SOURCE_RESOURCE_LIMIT")
        for root in (job, output.parent):
            v = os.statvfs(root)
            if v.f_bavail * v.f_frsize < budget["minimum_free_bytes"]:
                raise PrefixSourceError("SOURCE_FREE_SPACE_FLOOR")
        if output.exists() and sum(p.stat().st_size for p in output.iterdir()) > budget[
            "all_native_outputs_bytes"
        ]:
            raise PrefixSourceError("SOURCE_OUTPUT_LIMIT")

    manifest_hashes = [(roots[0], "manifest.json", plan["DWD_manifest_sha256"]),
                       (roots[0], "MATERIALIZATION.json", plan["DWD_materialization_sha256"]),
                       (roots[1], "manifest.json", plan["ODS_manifest_sha256"])]
    before = {}
    verified = []
    for root, relative, expected in manifest_hashes:
        guard()
        path = checked_member(root, relative)
        st = path.stat()
        if sha_file(path) != expected:
            raise PrefixSourceError("SOURCE_MANIFEST_CHANGED")
        before[str(path)] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if relative == "manifest.json" and (root / "READY").read_text().strip() != (
            "sha256:" + expected
        ):
            raise PrefixSourceError("SOURCE_READY_IDENTITY")
    materialization = json.loads((roots[0] / "MATERIALIZATION.json").read_text())
    ods_manifest = json.loads((roots[1] / "manifest.json").read_text())
    allowed_dwd = {m["path"]: m for t in materialization["layout"].values() for m in t["files"]}
    allowed_ods = {m["path"]: m for m in ods_manifest["content"]["files"]}
    for members, inventory in [(plan["DWD_members"], allowed_dwd),
                               (plan["ODS_members"], allowed_ods)]:
        if len({m["path"] for m in members}) != len(members):
            raise PrefixSourceError("DUPLICATE_MEMBER")
        if any(inventory.get(m["path"]) != m for m in members):
            raise PrefixSourceError("SOURCE_MEMBER_NOT_IN_MANIFEST")
    for root, members in zip(roots, (plan["DWD_members"], plan["ODS_members"]), strict=True):
        for member in members:
            guard()
            path = checked_member(root, member["path"])
            st = path.stat()
            if (st.st_size != member["size_bytes"]
                    or sha_file(path) != member["sha256"].removeprefix("sha256:")):
                raise PrefixSourceError("SOURCE_MEMBER_CHANGED")
            identity = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            now = path.stat()
            if identity != (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns):
                raise PrefixSourceError("SOURCE_MUTATED")
            before[str(path)] = identity
            verified.append({"root": str(root), **member, "device": st.st_dev,
                             "inode": st.st_ino, "mtime_ns": st.st_mtime_ns})
    save("evidence/source-members-verified.safe.json", {
        "status": "SELECTED_MEMBER_SHA256_PASS", "members": verified,
        "verified_bytes": sum(m["size_bytes"] for m in verified),
        "elapsed_seconds": time.monotonic() - started,
        "whole_base_reverification": False,
    })
    selected = json.loads(selection_path.read_text())["proposed"]
    connection = duckdb.connect()
    connection.execute("SET memory_limit=?", [budget["duckdb_memory_limit"]])
    connection.execute("SET threads=1")
    connection.execute("SET TimeZone='Asia/Shanghai'")
    connection.execute("SET autoinstall_known_extensions=false")
    connection.execute("SET autoload_known_extensions=false")
    connection.execute("SET temp_directory=?", [str(job / "tmp")])
    connection.execute("SET max_temp_directory_size='128MB'")
    connection.execute("CREATE TABLE selected(code VARCHAR,security_id VARCHAR,listing_id VARCHAR,"
                       "venue_id VARCHAR,company_id VARCHAR,l DATE,r DATE)")
    connection.executemany("INSERT INTO selected VALUES (?,?,?,?,?,?,?)", [
        (s["code"], s["security_id"], s["listing_id"], s["venue_id"], s["company_id"],
         s["proposed_start"], s["proposed_through"]) for s in selected
    ])
    tables: dict[str, list[str]] = defaultdict(list)
    for member in plan["DWD_members"]:
        tables[member["table"]].append(str(roots[0] / member["path"]))
    output.mkdir()
    receipts = []

    def materialize(name: str, sql: str, parameters: list[Any]) -> None:
        guard()
        tick = time.monotonic()
        relation = connection.sql(sql, params=parameters)
        (job / "logs" / (name + "-query.txt")).write_text(sql + "\n" + relation.explain())
        path = output / (name + ".parquet")
        relation.write_parquet(str(path), compression="zstd")
        p = pq.ParquetFile(path)
        receipts.append({"table": name, "path": str(path), "rows": p.metadata.num_rows,
                         "bytes": path.stat().st_size, "sha256": sha_file(path),
                         "schema_sha256": hashlib.sha256(str(p.schema_arrow).encode()).hexdigest(),
                         "elapsed_seconds": time.monotonic() - tick,
                         "disposition": "BUILD_ONCE_JOB_LOCAL_TMP"})
        save("evidence/" + name + "-materialized.safe.json", receipts[-1])
        guard()

    for table, paths in tables.items():
        connection.from_parquet(paths, union_by_name=True, hive_partitioning=False).create_view(
            "input_" + table
        )
        condition = "knowledge_from<=? AND (knowledge_to IS NULL OR knowledge_to>?)"
        parameters = [plan["snapshot"], plan["snapshot"]]
        if table in {"dwd_security_bar", "dwd_tradability_state", "dwd_observation_gap"}:
            field = ("affected_effective_at" if table == "dwd_observation_gap"
                     else "observation_start")
            condition += (" AND EXISTS(SELECT 1 FROM selected s WHERE s.listing_id=t.listing_id"
                          f" AND CAST(timezone('Asia/Shanghai',t.{field}) AS DATE)"
                          " BETWEEN s.l AND s.r)")
            if table in {"dwd_security_bar", "dwd_observation_gap"}:
                condition += " AND interval_size='1d'"
        elif table == "dwd_corporate_action":
            condition += (" AND EXISTS(SELECT 1 FROM selected s WHERE s.security_id=t.security_id"
                          " AND t.ex_date BETWEEN s.l AND s.r)")
        elif table == "dwd_corporate_action_term":
            condition += (" AND corporate_action_id IN (SELECT corporate_action_id FROM "
                          "input_dwd_corporate_action a JOIN selected s ON "
                          "s.security_id=a.security_id AND a.ex_date BETWEEN s.l AND s.r)")
        elif table == "dwd_trading_calendar":
            condition += (" AND EXISTS(SELECT 1 FROM selected s WHERE s.venue_id=t.venue_id"
                          " AND t.trade_date BETWEEN s.l AND s.r)")
        elif table == "dwd_quality_exception":
            condition += (" AND CAST(timezone('Asia/Shanghai',affected_effective_at) AS DATE) "
                          "BETWEEN DATE '2021-01-01' AND DATE '2021-12-31'")
        else:
            key = {"dwd_listing": "listing_id", "dwd_security": "security_id",
                   "dwd_company_identifier": "company_id"}[table]
            condition += f" AND {key} IN (SELECT {key} FROM selected)"
        materialize(table, f"SELECT * FROM input_{table} t WHERE {condition}", parameters)
    raw_members = {m["path"]: m for m in plan["ODS_members"]}
    for name, relative, field in [
        ("main", "payload/canonical/daily_ohlcv_none.parquet", "date"),
        ("hotfix", "payload/daily/daily_ohlcv_none_hotfix.parquet", "date"),
        ("actions", "payload/canonical/ref_corporate_actions.parquet", "ex_date"),
        ("visibility", "payload/canonical/ref_adjustment_factor_pit_exceptions.parquet",
         "effective_date"),
        ("dividends", "payload/canonical/dividend_history.parquet", "ex_dividend_date"),
    ]:
        connection.from_parquet(str(roots[1] / relative), hive_partitioning=False).create_view(
            "raw_" + name
        )
        codes = [s["code"] for s in selected]
        if any(len(c) != 6 or not c.isdigit() for c in codes):
            raise PrefixSourceError("SELECTION_CODE")
        literals = ",".join("'" + c + "'" for c in sorted(codes))
        sql = (f"WITH bounded AS MATERIALIZED (SELECT * FROM raw_{name} WHERE "
               f"stock_code IN ({literals}) AND try_cast({field} AS DATE) BETWEEN "
               "DATE '2021-01-01' AND DATE '2021-12-31') SELECT t.* FROM bounded t "
               f"JOIN selected s ON t.stock_code=s.code AND try_cast(t.{field} AS DATE) "
               "BETWEEN s.l AND s.r")
        materialize("ods_" + name, sql, [])
    data = {r["table"]: pq.ParquetFile(r["path"]).read().to_pylist() for r in receipts}
    raw_hashes = {"main": raw_members["payload/canonical/daily_ohlcv_none.parquet"]["sha256"],
                  "hotfix": raw_members["payload/daily/daily_ohlcv_none_hotfix.parquet"]["sha256"]}
    census = validate_selected_native(selected, data, raw_hashes)
    for name, identity in before.items():
        st = Path(name).stat()
        if identity != (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns):
            raise PrefixSourceError("SOURCE_MUTATED")
    guard()
    result = {"kind": VERSION, "UTC": datetime.now(timezone.utc).isoformat(), **census,
              "selection_sha256": plan["selection_sha256"],
              "request_sha256": plan["request_sha256"], "plan_sha256": sha_file(plan_path),
              "materializations": receipts, "source_files_stat_unchanged": True,
              "elapsed_seconds": time.monotonic() - started,
              "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
              "known_gap_rows": len(data["dwd_observation_gap"]),
              "quality_exception_rows": len(data["dwd_quality_exception"]),
              "native_action_rows": len(data["dwd_corporate_action"]),
              "raw_action_rows": len(data["ods_actions"]),
              "dividend_rows": len(data["ods_dividends"]),
              "historical_provider_acquisition": "UNAVAILABLE_RETAINED",
              "reference_or_candidate_calculated": False, "new_network_requests": 0}
    save("evidence/native-prefix-census.safe.json", result)
    connection.close()
    return result
