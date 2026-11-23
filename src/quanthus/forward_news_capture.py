"""Isolated P10 raw forward news capture; no Base, strategy or consumer publication.

The receipt records clocks measured by this producer. Vendor publication clocks stay
separate; midnight announcement dates are not promoted to precise publication times.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quanthus.p10_news_pdf_archive import DrivePdfArchive, atomic

VERSION = "p10-forward-news-raw-v1"
BJT = ZoneInfo("Asia/Shanghai")
SINA = "https://zhibo.sina.com.cn/api/zhibo/feed"
CNINFO = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_PREFIX = "https://static.cninfo.com.cn/"
PRIORITY = re.compile(
    r"业绩|盈利|利润|年度报告|季度报告|半年度|重大合同|中标|增持|减持|并购|重组|收购|"
    r"控制权|实际控制人|监管|处罚|调研|投资者关系活动|问询|关注函|异常波动"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decode_bytes(raw: bytes, limit: int) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("DECODED_RESPONSE_LIMIT")
    return raw


def parse_entity(raw: bytes, limit: int) -> Any:
    return json.loads(decode_bytes(raw, limit).decode("utf-8-sig"))


def source_clock(
    value: Any, *, epoch_ms: bool = False, timezone_evidence: str = ""
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "raw": value,
        "published_at": None,
        "precision": "unknown",
        "reason": None,
    }
    try:
        if epoch_ms:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError("INVALID_SOURCE_TIME")
            stamp = datetime.fromtimestamp(int(value) / 1000, timezone.utc).astimezone(BJT)
        else:
            if "+0800" not in timezone_evidence:
                raise ValueError("SOURCE_TIMEZONE_UNVERIFIED")
            stamp = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJT)
        result["reported_date"] = stamp.date().isoformat()
        if stamp.hour == stamp.minute == stamp.second == stamp.microsecond == 0:
            result.update(precision="day", reason="DATE_ONLY_OR_MIDNIGHT_PLACEHOLDER")
        else:
            result.update(published_at=stamp.isoformat(), precision="second", reason=None)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        result["reason"] = (
            str(exc) if str(exc) == "SOURCE_TIMEZONE_UNVERIFIED" else "INVALID_SOURCE_TIME"
        )
    return result


def plain(value: str) -> str:
    return unescape(re.sub(r"<[^>]*>", "", value)).strip()


def normalize_news(item: dict[str, Any], timezone_evidence: str) -> dict[str, Any]:
    if (
        not isinstance(item.get("id"), (str, int))
        or isinstance(item.get("id"), bool)
        or not str(item["id"])
    ):
        raise ValueError("NEWS_NATIVE_ID_REQUIRED")
    native = str(item["id"])
    rich = str(item.get("rich_text") or "")
    body = plain(rich)
    try:
        ext = json.loads(item.get("ext") or "{}")
    except (TypeError, ValueError):
        ext = {"unparsed": item.get("ext")}
    if not isinstance(ext, dict):
        ext = {"unparsed": ext}
    heading = re.match(r"【([^】]+)】", body)
    return {
        "source": "sina-7x24",
        "source_record_id": native,
        "source_document_id": native,
        "source_url": str(item.get("docurl") or ext.get("docurl") or SINA),
        "title": heading.group(1) if heading else body[:120],
        "title_basis": "derived_from_source_rich_text",
        "body": body,
        "source_rich_text": rich,
        "source_publication": source_clock(
            item.get("create_time"), timezone_evidence=timezone_evidence
        ),
        "source_update_time_raw": item.get("update_time"),
        "source_entities_raw": ext.get("stocks", []),
        "source_tags_raw": item.get("tag", []),
        "source_deleted_raw": item.get("is_delete"),
        "security_ids": None,
        "identity_status": "QUALIFIED_PIT_MAPPING_NOT_BOUND",
    }


def normalize_announcement(item: dict[str, Any]) -> dict[str, Any]:
    if not item.get("announcementId"):
        raise ValueError("ANNOUNCEMENT_NATIVE_ID_REQUIRED")
    native = str(item["announcementId"])
    code = str(item.get("secCode") or "")
    title = plain(str(item.get("announcementTitle") or ""))
    adjunct = str(item.get("adjunctUrl") or "")
    url = PDF_PREFIX + adjunct.lstrip("/") if adjunct else None
    return {
        "source": "cninfo-announcements",
        "source_record_id": native + "@" + code,
        "source_document_id": native,
        "source_url": url,
        "title": title,
        "source_categories_raw": item.get("announcementType"),
        "source_page_column": item.get("pageColumn"),
        "source_entities_raw": [
            {"code": code, "name": item.get("secName"), "org_id": item.get("orgId")}
        ],
        "source_publication": source_clock(item.get("announcementTime"), epoch_ms=True),
        "source_storage_time_raw": item.get("storageTime"),
        "priority_body": bool(PRIORITY.search(title)),
        "body": None,
        "document_sha256": None,
        "body_state": "PRIORITY_QUEUE" if PRIORITY.search(title) else "ARCHIVE_ONLY_NO_TEXT",
        "pdf_archive_state": "pdf_archive_pending",
        "security_ids": None,
        "identity_status": "QUALIFIED_PIT_MAPPING_NOT_BOUND",
    }


def limited_markdown(
    text: str, *, title: str, url: str, document_sha: str, max_bytes: int = 32767
) -> tuple[bytes, bool]:
    header = f"# {title}\n\nSource: {url}\n\nDocument SHA256: {document_sha}\n\n"
    budget = max_bytes - len(header.encode()) - 80
    if budget < 0:
        raise ValueError("MARKDOWN_HEADER_LIMIT")
    raw = text.encode()
    truncated = len(raw) > budget
    excerpt = raw[:budget].decode("utf-8", errors="ignore")
    suffix = (
        "\n\n[TRUNCATED: see original document and extraction receipt]\n" if truncated else "\n"
    )
    out = (header + excerpt + suffix).encode()
    if len(out) > max_bytes:
        raise ValueError("MARKDOWN_BYTES_LIMIT")
    return out, truncated


def clean_chinese_pages(pages: list[str]) -> str:
    """Deterministic conservative paragraph joining and repeated margin removal."""
    counts: dict[str, int] = {}
    for page in pages:
        lines = [x.strip() for x in page.splitlines() if x.strip()]
        for line in set(lines[:2] + lines[-2:]):
            counts[line] = counts.get(line, 0) + 1
    repeated = {k for k, v in counts.items() if len(pages) >= 3 and v >= max(3, len(pages) * 0.6)}
    out: list[str] = []
    for page in pages:
        lines = page.splitlines()
        nonempty = [i for i, line in enumerate(lines) if line.strip()]
        margins = set(nonempty[:2] + nonempty[-2:])
        for i, value in enumerate(lines):
            line = value.strip()
            if i in margins and (
                line in repeated or re.fullmatch(r"(?:第\s*)?\d+\s*(?:页|/\s*\d+)?", line)
            ):
                continue
            if (
                out
                and out[-1]
                and line
                and re.search(r"[\u4e00-\u9fff]$", out[-1])
                and re.match(r"^[\u4e00-\u9fff]", line)
            ):
                out[-1] += line
            else:
                out.append(line)
        out.append("")
    return "\n".join(out).strip()


class Capture:
    def __init__(self, root: Path, config: dict[str, Any], code_revision: str):
        self.root = root.resolve()
        self.config = config
        self.code_revision = code_revision
        self.code_sha = sha(Path(__file__).read_bytes())
        self.host_last: dict[str, float] = {}
        self.requests = 0
        self.bytes = 0
        self.started = time.monotonic()
        self.receipts: list[dict[str, Any]] = []
        self.layer_bytes: dict[str, int] = {}
        self.holds: dict[str, Any] = (
            json.loads((self.root / "SOURCE-HOLDS.json").read_bytes())
            if (self.root / "SOURCE-HOLDS.json").exists()
            else {}
        )
        self.run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
        )
        self.run_root = self.root / "runs" / self.run_id

    def put(self, raw: bytes, kind: str) -> dict[str, Any]:
        digest = sha(raw)
        target = self.root / "objects" / kind / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        before = now()
        if target.exists():
            if sha(target.read_bytes()) != digest:
                raise ValueError("IMMUTABLE_OBJECT_CONFLICT")
        else:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".pending-", delete=False
            ) as f:
                tmp = Path(f.name)
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                if sha(target.read_bytes()) != digest:
                    raise ValueError("IMMUTABLE_OBJECT_CONFLICT") from None
            finally:
                tmp.unlink(missing_ok=True)
            self.layer_bytes[kind] = self.layer_bytes.get(kind, 0) + len(raw)
            fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return {
            "path": str(target),
            "sha256": digest,
            "size_bytes": len(raw),
            "commit_interval": {"start": before, "end": now()},
        }

    def fetch(
        self,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        post: bool = False,
        pdf: bool = False,
    ) -> tuple[bytes, dict[str, Any]]:
        limits = self.config["budgets"]
        if (
            self.requests >= limits["max_requests"]
            or time.monotonic() - self.started > limits["max_wall_seconds"]
        ):
            raise ValueError("RUN_REQUEST_OR_TIME_LIMIT")
        host = urlparse(url).hostname
        if urlparse(url).scheme != "https" or host not in self.config["allowed_hosts"]:
            raise ValueError("SOURCE_HOST_NOT_ALLOWED")
        hold = self.holds.get(str(host), {})
        if hold.get("manual_clear_required") or hold.get("until", 0) > time.time():
            raise ValueError("SOURCE_HELD_BY_RATE_OR_ACCESS_POLICY")
        wait = self.config["min_host_interval_seconds"] - (
            time.monotonic() - self.host_last.get(str(host), 0)
        )
        if wait > 0:
            time.sleep(wait)
        request_url = url if post or not params else url + "?" + urlencode(params)
        data = urlencode(params or {}).encode() if post else None
        request = Request(
            request_url,
            data=data,
            headers={
                "User-Agent": "Quanthus-P10-Forward-Trial/0.1 (+https://github.com/rv198-star/Quanthus)",
                "Accept-Encoding": "gzip",
                **(
                    {
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                    }
                    if post
                    else {}
                ),
                "Referer": "https://www.cninfo.com.cn/"
                if "cninfo" in str(host)
                else "https://finance.sina.com.cn/",
            },
        )
        receipt: dict[str, Any] = {
            "source": source,
            "request_url": request_url,
            "request_parameters": params,
            "request_started_at": now(),
        }
        self.requests += 1
        self.host_last[str(host)] = time.monotonic()
        cap = limits["max_pdf_bytes"] if pdf else limits["max_wire_bytes"]
        try:
            with urlopen(request, timeout=limits["request_timeout_seconds"]) as response:
                raw = response.read(cap + 1)
                receipt.update(
                    http_status=response.status,
                    content_encoding=response.headers.get("Content-Encoding"),
                    content_type=response.headers.get("Content-Type"),
                    source_http_date=response.headers.get("Date"),
                    response_completed_at=now(),
                )
        except HTTPError as exc:
            retry_after = exc.headers.get("Retry-After")
            receipt.update(
                http_status=exc.code,
                response_completed_at=now(),
                retry_after_raw=retry_after,
                error="HTTP_ERROR_NO_AUTOMATIC_RETRY",
            )
            if exc.code in {401, 403, 429}:
                seconds = 900.0
                if retry_after:
                    try:
                        seconds = max(0.0, float(retry_after))
                    except ValueError:
                        with contextlib.suppress(ValueError, TypeError):
                            seconds = max(
                                0.0, parsedate_to_datetime(retry_after).timestamp() - time.time()
                            )
                self.holds[str(host)] = {
                    "manual_clear_required": exc.code in {401, 403},
                    "until": time.time() + seconds,
                    "reason": exc.code,
                    "recorded_at": now(),
                }
                tmp = self.root / ".source-holds.pending"
                tmp.write_bytes(encoded(self.holds))
                os.replace(tmp, self.root / "SOURCE-HOLDS.json")
            self.receipts.append(receipt)
            raise ValueError("SOURCE_HTTP_" + str(exc.code)) from exc
        except Exception as exc:
            receipt.update(error=type(exc).__name__, response_completed_at=now())
            self.receipts.append(receipt)
            raise
        self.bytes += len(raw)
        receipt["truncated"] = len(raw) > cap
        wire_kind = (
            "post-cutoff-wire"
            if self.config.get("case")
            and datetime.fromisoformat(receipt["response_completed_at"])
            >= datetime.fromisoformat(self.config["case"]["input_cutoff"])
            else "wire"
        )
        receipt["raw"] = (
            {"sha256": sha(raw), "size_bytes": len(raw), "storage": "BOUNDED_PDF_TMP"}
            if pdf
            else self.put(raw, wire_kind)
        )
        self.receipts.append(receipt)
        if len(raw) > cap or self.bytes > limits["max_run_bytes"]:
            raise ValueError("SOURCE_RESPONSE_BYTE_LIMIT")
        return raw, receipt

    def item(
        self, db: sqlite3.Connection, payload: dict[str, Any], evidence: dict[str, Any]
    ) -> bool:
        payload["normalizer_version"] = VERSION
        semantic = sha(
            encoded(
                {
                    "payload": payload,
                    "code_sha256": self.code_sha,
                    "config_sha256": sha(encoded(self.config)),
                }
            )
        )
        key = payload["source"] + ":" + payload["source_record_id"]
        previous = db.execute(
            "SELECT semantic,member_sha,first_seen FROM items WHERE id=?", [key]
        ).fetchone()
        if previous and previous[0] == semantic:
            return False
        case = self.config.get("case", {})
        haystack = encoded(payload).decode().lower()
        case_related = bool(case) and any(
            str(x).lower() in haystack for x in case.get("match_terms", [])
        )
        case_role = None
        if case_related:
            case_role = (
                "INPUT_VISIBLE_BEFORE_CUTOFF"
                if datetime.fromisoformat(evidence["response_completed_at"])
                < datetime.fromisoformat(case["input_cutoff"])
                else "POST_CUTOFF_RESULT_CAPTURE"
            )
        record = {
            "schema": "quanthus.p10.forward-text-record.v1",
            "payload": payload,
            "semantic_sha256": semantic,
            "source_artifact_known_at": evidence["response_completed_at"],
            "first_seen_at": previous[2] if previous else evidence["response_completed_at"],
            "version_first_seen_at": evidence["response_completed_at"],
            "case_id": case.get("case_id") if case_related else None,
            "case_role": case_role,
            "case_policy_body_sha256": case.get("definition_body_sha256") if case_related else None,
            "raw_source_receipt": evidence,
            "previous_member_sha256": previous[1] if previous else None,
            "producer_code_revision": self.code_revision,
            "producer_code_sha256": self.code_sha,
            "qualification": "RAW_CAPTURE_ONLY",
            "Base_release": None,
            "period": "TRIAL_20261124_30"
            if datetime.now(BJT).date().isoformat() < "2026-12-01"
            else "POST_20261201_UNQUALIFIED",
        }
        member = self.put(
            encoded(record),
            "case-input"
            if case_role == "INPUT_VISIBLE_BEFORE_CUTOFF"
            else "case-result-capture"
            if case_role
            else "normalized",
        )
        event = {
            "schema": "quanthus.p10.forward-capture-event.v1",
            "event_id": uuid.uuid4().hex,
            "run_id": self.run_id,
            "event": "SOURCE_REVISION_OBSERVED" if previous else "FIRST_OBSERVED",
            "source_key": key,
            "member": member,
            "publisher": socket.gethostname() + ":p10-forward-news",
            "publication_commit_interval": member["commit_interval"],
            "publication_observed_at_actual": now(),
            "consumer_available_at": None,
            "consumer_availability_reason": "NO_CONSUMER_CHANNEL_RECEIPT",
            "case_role": case_role,
            "channel": "private-file-cas",
            "previous_member_sha256": previous[1] if previous else None,
        }
        self.put(encoded(event), "events")
        db.execute(
            "INSERT OR REPLACE INTO items VALUES(?,?,?,?)",
            [key, semantic, member["sha256"], record["first_seen_at"]],
        )
        if payload.get("source") == "cninfo-announcements" and payload.get("source_url"):
            job_id = sha(encoded({"metadata_sha": member["sha256"], "url": payload["source_url"]}))
            db.execute(
                "INSERT OR IGNORE INTO bodies("
                "job_id,url,title,priority,state,attempts,next_try,member_sha) "
                "VALUES(?,?,?,?,'pdf_archive_pending',0,0,?)",
                [
                    job_id,
                    payload["source_url"],
                    payload["title"],
                    int(bool(payload.get("priority_body"))),
                    member["sha256"],
                ],
            )
        db.commit()
        return True

    def news(self, db: sqlite3.Connection) -> dict[str, Any]:
        count = new = 0
        boundary = False
        for page in range(1, self.config["news_max_pages"] + 1):
            raw, receipt = self.fetch(
                "sina-7x24", SINA, {"page": page, "page_size": 100, "zhibo_id": 152}
            )
            obj = parse_entity(raw, self.config["budgets"]["max_entity_bytes"])["result"]
            if obj["status"]["code"] != 0:
                raise ValueError("SINA_SOURCE_ERROR")
            rows = obj["data"]["feed"]["list"]
            if not isinstance(rows, list):
                raise ValueError("SINA_INVALID_ROWS")
            seen = False
            for row in rows:
                count += 1
                added = self.item(db, normalize_news(row, str(obj.get("timestamp", ""))), receipt)
                new += int(added)
                seen = seen or not added
            if (
                seen
                or not rows
                or any(
                    str(x.get("create_time", ""))[:10] < datetime.now(BJT).date().isoformat()
                    for x in rows
                )
            ):
                boundary = True
                break
        return {
            "source": "sina-7x24",
            "rows_seen": count,
            "new_versions": new,
            "overlap_or_start_boundary_reached": boundary,
            "coverage": "VISIBLE_FEED_SCAN" if boundary else "PARTIAL_PAGE_BUDGET",
        }

    def announcements(self, db: sqlite3.Connection) -> dict[str, Any]:
        partitions = []
        for partition in self.config["ann_partitions"]:
            try:
                partitions.append(self.announcements_partition(db, partition))
            except Exception as exc:
                partitions.append(
                    {
                        "column": partition["column"],
                        "provider_pagination_complete": False,
                        "status": "PARTIAL_OR_BLOCKED",
                        "reason": type(exc).__name__ + ":" + str(exc),
                    }
                )
        if len(partitions) == 1:
            return partitions[0]
        return {
            "partitions": partitions,
            "provider_pagination_complete": all(
                p["provider_pagination_complete"] for p in partitions
            ),
            "unique_source_rows": sum(p.get("unique_source_rows", 0) for p in partitions),
            "status": "CAPTURED"
            if all(p["provider_pagination_complete"] for p in partitions)
            else "PARTIAL_SOURCE_COVERAGE",
            "A_share_universe_qualification": "PENDING_QUALIFIED_PIT_MAPPING_AND_VENDOR_SCOPE",
        }

    def announcements_partition(
        self, db: sqlite3.Connection, partition: dict[str, str]
    ) -> dict[str, Any]:
        count = new = 0
        ids: set[str] = set()
        totals: set[int] = set()
        end = overlap = repeated_page = False
        page_keys: set[str] = set()
        db.execute("CREATE TABLE IF NOT EXISTS scan_state(id TEXT PRIMARY KEY,value TEXT)")
        state_id = "ann-full:" + partition["column"]
        prior = db.execute("SELECT value FROM scan_state WHERE id=?", [state_id]).fetchone()
        prior_state = json.loads(prior[0]) if prior else {}
        day = datetime.now(BJT).date().isoformat()
        full_scan = (
            prior_state.get("day") != day
            or time.time() - prior_state.get("at", 0) > self.config["ann_full_scan_seconds"]
        )
        for page in range(1, self.config["ann_max_pages"] + 1):
            day = datetime.now(BJT).date().isoformat()
            raw, receipt = self.fetch(
                "cninfo-announcements",
                CNINFO,
                {
                    "pageNum": page,
                    "pageSize": 100,
                    "column": partition["column"],
                    "tabName": "fulltext",
                    "plate": partition["plate"],
                    "stock": "",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": day + "~" + day,
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "false",
                },
                post=True,
            )
            obj = parse_entity(raw, self.config["budgets"]["max_entity_bytes"])
            rows = obj.get("announcements") or []
            if not isinstance(rows, list) or "hasMore" not in obj:
                raise ValueError("CNINFO_INVALID_ROWS")
            totals.add(int(obj["totalAnnouncement"]))
            page_key = sha(
                encoded(
                    sorted(str(x.get("announcementId")) + "@" + str(x.get("secCode")) for x in rows)
                )
            )
            if rows and page_key in page_keys:
                repeated_page = True
                break
            page_keys.add(page_key)
            seen = False
            for row in rows:
                item = normalize_announcement(row)
                ids.add(item["source_record_id"])
                count += 1
                added = self.item(db, item, receipt)
                new += int(added)
                seen = seen or not added
            if not obj["hasMore"]:
                end = True
                break
            if not full_scan and seen:
                overlap = True
                break
            if not rows:
                break
        complete = end and len(totals) == 1 and len(ids) >= next(iter(totals))
        if complete or repeated_page:
            db.execute(
                "INSERT OR REPLACE INTO scan_state VALUES(?,?)",
                [
                    state_id,
                    encoded(
                        {
                            "day": day,
                            "at": time.time(),
                            "complete": complete,
                            "reason": "PROVIDER_REPEATED_PAGE" if repeated_page else None,
                        }
                    ).decode(),
                ],
            )
            db.commit()
        return {
            "source": "cninfo-announcements",
            "status": "PARTIAL_SOURCE_COVERAGE"
            if repeated_page or (full_scan and not complete)
            else "CAPTURED",
            "column": partition["column"],
            "plate": partition["plate"],
            "pagination_blocker": "PROVIDER_REPEATED_PAGE"
            if repeated_page
            else prior_state.get("reason"),
            "rows_seen": count,
            "unique_source_rows": len(ids),
            "provider_totals": sorted(totals),
            "new_versions": new,
            "provider_pagination_complete": complete,
            "scan_mode": "FULL_PROVIDER_SCAN" if full_scan else "INCREMENTAL",
            "known_record_overlap_reached": overlap,
            "old_page_revision_rescan_seconds": self.config["ann_full_scan_seconds"],
            "A_share_universe_qualification": "PENDING_QUALIFIED_PIT_MAPPING_AND_VENDOR_SCOPE",
            "precise_publication": "DATE_ONLY_WHERE_MIDNIGHT; NULL_NOT_INFERRED",
        }

    def ocr_failed_pages(self, path: Path, pages: list[int]) -> dict[str, Any]:
        policy = self.config["ocr"]
        if not pages:
            return {"pages": [], "state": "NOT_NEEDED"}
        try:
            with tempfile.TemporaryDirectory(prefix="p10-ocr-result-") as tmp:
                output = Path(tmp) / "ocr.json"
                result = subprocess.run(
                    [
                        policy["python"],
                        "-m",
                        "quanthus.p10_local_ocr",
                        "--pdf",
                        str(path),
                        "--pages",
                        ",".join(str(p) for p in pages[: policy["max_pages_per_pdf"]]),
                        "--expected-models-sha256",
                        policy["models_sha256"],
                        "--output",
                        str(output),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=policy["timeout_seconds"],
                    check=False,
                )
                if result.returncode or not output.is_file():
                    return {"pages": [], "state": "FAILED_LOCAL_OCR"}
                if output.stat().st_size > 2 * 1024**2:
                    return {"pages": [], "state": "FAILED_OCR_OUTPUT_LIMIT"}
                data: dict[str, Any] = json.loads(output.read_bytes())
                if data["models_sha256"] != policy["models_sha256"]:
                    raise ValueError("OCR_MODEL_IDENTITY_MISMATCH")
                data["state"] = "ATTEMPTED_LOCAL"
                return data
        except Exception as exc:
            return {"pages": [], "state": "FAILED_LOCAL_OCR", "reason": type(exc).__name__}

    def extract_document(self, path: Path, *, title: str, url: str, digest: str) -> dict[str, Any]:
        started = time.monotonic()
        extractor = self.config.get("pdftotext", "/usr/bin/pdftotext")
        version = self.config["text_extractor_version"]
        key = sha(encoded({"pdf_sha256": digest, "extractor_version": version}))
        cached = self.root / "extraction-cache" / (key + ".json")
        if cached.exists():
            result: dict[str, Any] = json.loads(cached.read_bytes())
            if result["pdf_sha256"] != digest or result["extractor_version"] != version:
                raise ValueError("EXTRACTION_CACHE_IDENTITY_MISMATCH")
            return result
        if not Path(extractor).is_file():
            return {
                "state": "TEXT_EXTRACTOR_UNAVAILABLE",
                "markdown": None,
                "extractor_version": version,
            }
        try:
            checked = subprocess.run([extractor, "-v"], capture_output=True, timeout=5, check=False)
            installed = (checked.stdout + checked.stderr).decode(errors="replace").splitlines()[0]
            if installed != self.config["pdftotext_version_line"]:
                return {
                    "state": "TEXT_EXTRACTOR_VERSION_MISMATCH",
                    "markdown": None,
                    "actual_version": installed,
                }
            with tempfile.TemporaryDirectory(prefix="p10-text-extract-") as tmp:
                output = Path(tmp) / "document.txt"
                result_code = subprocess.run(
                    [
                        extractor,
                        "-layout",
                        "-f",
                        "1",
                        "-l",
                        "100",
                        "-enc",
                        "UTF-8",
                        str(path),
                        str(output),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                )
                if result_code.returncode or not output.is_file():
                    return {
                        "state": "TEXT_EXTRACTION_FAILED",
                        "markdown": None,
                        "extractor_version": version,
                    }
                with output.open("rb") as stream:
                    raw = stream.read(2 * 1024**2 + 1)
            pages = raw[: 2 * 1024**2].decode("utf-8", errors="replace").split("\f")
            if pages and not pages[-1].strip():
                pages.pop()
            failed_pages = [
                i + 1
                for i, text in enumerate(pages)
                if len(re.sub(r"\s", "", text)) < 20
                or text.count("\ufffd") > max(1, len(text) * 0.02)
            ]
            if not pages:
                pages = [""]
                failed_pages = [1]
            original_failures = list(failed_pages)
            garbled_before = sum(t.count("\ufffd") for t in pages) / max(
                1, sum(len(t) for t in pages)
            )
            ocr = self.ocr_failed_pages(path, failed_pages)
            recovered = []
            for entry in ocr["pages"]:
                page = entry["page"]
                if page not in original_failures:
                    raise ValueError("OCR_PAGE_IDENTITY_MISMATCH")
                if entry.get("state") == "OCR_TEXT" and entry["text"].strip():
                    pages[page - 1] = entry["text"]
                    recovered.append(page)
            failed_pages = [p for p in original_failures if p not in recovered]
            text = clean_chinese_pages(pages)
            md, truncated = limited_markdown(
                text, title="Source document", url="content-addressed-PDF", document_sha=digest
            )
            result = {
                "state": ("PARTIAL_FAILED" if text.strip() else "FAILED")
                if failed_pages
                else "EXTRACTED",
                "pdf_sha256": digest,
                "extractor_version": version,
                "markdown": self.put(md, "markdown") if text.strip() else None,
                "truncated": truncated or len(raw) > 2 * 1024**2,
                "page_limit": 100,
                "pages_examined": len(pages),
                "failed_pages": failed_pages,
                "ocr_state": ocr["state"],
                "original_failed_pages": original_failures,
                "ocr_recovered_pages": recovered,
                "ocr_page_budget": self.config["ocr"]["max_pages_per_pdf"],
                "route": "OCR" if original_failures else "TEXT",
                "wall_seconds": time.monotonic() - started,
                "garbled_character_rate_before_ocr": garbled_before,
                "garbled_character_rate_after_ocr": text.count("\ufffd") / max(1, len(text)),
                "table_review": "PENDING_DECLARED_WEEK_SAMPLE_REVIEW",
                "api_calls": 0,
                "cache_key": key,
                "extracted_at": now(),
            }
            atomic(cached, encoded(result))
            return result
        except Exception as exc:
            return {
                "state": "TEXT_EXTRACTION_FAILED",
                "reason": type(exc).__name__,
                "markdown": None,
                "extractor_version": version,
                "api_calls": 0,
            }

    def finish_body(
        self,
        db: sqlite3.Connection,
        job_id: str,
        member_sha: str,
        priority: int,
        source: dict[str, Any],
        extraction: dict[str, Any],
        cloud: dict[str, Any],
        path: Path,
    ) -> None:
        if (
            source["metadata_member_sha256"] != member_sha
            or cloud["sha256"] != source["sha256"]
            or cloud["bytes"] != source["bytes"]
            or cloud["folder_id"] != self.config["pdf_storage"]["folder_id"]
            or cloud.get("verification") != "REMOTE_FULL_READBACK_SHA256_AND_SIZE"
            or not cloud.get("verified_at")
        ):
            raise ValueError("PDF_ARCHIVE_RECEIPT_MISMATCH")
        if path.exists() and (
            sha(path.read_bytes()) != cloud["sha256"] or path.stat().st_size != cloud["bytes"]
        ):
            raise ValueError("PDF_STAGED_IDENTITY_MISMATCH")
        self.put(
            encoded(
                {
                    "schema": "quanthus.p10.document-sidecar.v2",
                    "job_id": job_id,
                    "metadata_member_sha256": member_sha,
                    "source": source,
                    "extraction": extraction,
                    "archive": cloud,
                    "priority": bool(priority),
                    "producer_code_revision": self.code_revision,
                    "recorded_at": now(),
                    "qualification": "RAW_CAPTURE_ONLY",
                }
            ),
            "document-events",
        )
        # The durable verified receipt precedes local deletion. Crash recovery reuses it.
        path.unlink(missing_ok=True)
        db.execute(
            "UPDATE bodies SET state='archived',attempts=attempts+1 WHERE job_id=?", [job_id]
        )

    def bodies(self, db: sqlite3.Connection) -> dict[str, Any]:
        policy = self.config["pdf_storage"]
        stage = self.root / "pdf-staging"
        stage.mkdir(exist_ok=True)
        jobs = self.root / "pdf-jobs"
        jobs.mkdir(exist_ok=True)
        rows = db.execute(
            "SELECT job_id,url,title,priority,attempts,member_sha FROM bodies "
            "WHERE state='pdf_archive_pending' AND next_try<=? "
            "ORDER BY priority DESC,rowid LIMIT ?",
            [time.time(), self.config["pdf_per_run"]],
        ).fetchall()
        archive = DrivePdfArchive(policy)
        done = failed = downloaded = background = 0
        alerts: list[str] = []
        for job_id, url, title, priority, attempts, member_sha in rows:
            path = stage / (job_id + ".pdf")
            job = jobs / job_id
            source_receipt = job / "SOURCE.json"
            extracted_receipt = job / "EXTRACTION.json"
            archived_receipt = job / "ARCHIVED.json"
            try:
                if archived_receipt.exists():
                    self.finish_body(
                        db,
                        job_id,
                        member_sha,
                        priority,
                        json.loads(source_receipt.read_bytes()),
                        json.loads(extracted_receipt.read_bytes()),
                        json.loads(archived_receipt.read_bytes()),
                        path,
                    )
                    db.commit()
                    done += 1
                    continue
                if not priority and background >= self.config["pdf_background_per_run"]:
                    continue
                if time.monotonic() - self.started > self.config["budgets"]["max_wall_seconds"]:
                    alerts.append("PDF_RUN_TIME_BUDGET")
                    break
                staged = list(stage.glob("*.pdf"))
                used = sum(p.stat().st_size for p in staged)
                if not path.exists():
                    if not priority and used >= policy["staging_max_bytes"] * 0.8:
                        alerts.append("BACKGROUND_PAUSED_STAGING_NEAR_LIMIT")
                        continue
                    if (
                        len(staged) >= policy["staging_max_files"]
                        or used + self.config["budgets"]["max_pdf_bytes"]
                        > policy["staging_max_bytes"]
                        or shutil.disk_usage(stage).free
                        < policy["free_floor_bytes"] + self.config["budgets"]["max_pdf_bytes"]
                    ):
                        alerts.append("PDF_STAGING_FULL_DOWNLOAD_PAUSED")
                        break
                    raw, evidence = self.fetch("cninfo-document", url, pdf=True)
                    raw = decode_bytes(raw, self.config["budgets"]["max_pdf_bytes"])
                    if not raw.startswith(b"%PDF"):
                        raise ValueError("SOURCE_DOCUMENT_NOT_PDF")
                    atomic(path, raw)
                    atomic(
                        source_receipt,
                        encoded(
                            {
                                "metadata_member_sha256": member_sha,
                                "source_url": url,
                                "source_receipt": evidence,
                                "sha256": sha(raw),
                                "bytes": len(raw),
                            }
                        ),
                    )
                    downloaded += len(raw)
                if (
                    path.is_symlink()
                    or path.stat().st_size > self.config["budgets"]["max_pdf_bytes"]
                ):
                    raise ValueError("PDF_STAGED_PATH_INVALID")
                digest = sha(path.read_bytes())
                if source_receipt.exists():
                    source = json.loads(source_receipt.read_bytes())
                    if source["sha256"] != digest or source["metadata_member_sha256"] != member_sha:
                        raise ValueError("PDF_STAGED_IDENTITY_MISMATCH")
                else:
                    source = {
                        "metadata_member_sha256": member_sha,
                        "source_url": url,
                        "sha256": digest,
                        "bytes": path.stat().st_size,
                        "source_receipt": None,
                        "reason": "CRASH_BEFORE_SOURCE_RECEIPT_COMMIT",
                    }
                    atomic(source_receipt, encoded(source))
                if not extracted_receipt.exists():
                    extraction = (
                        self.extract_document(path, title=title, url=url, digest=digest)
                        if priority
                        else {"state": "BACKGROUND_ARCHIVE_ONLY", "markdown": None}
                    )
                    atomic(extracted_receipt, encoded(extraction))
                if archived_receipt.exists():
                    cloud = json.loads(archived_receipt.read_bytes())
                    if (
                        cloud["sha256"] != digest
                        or cloud["bytes"] != path.stat().st_size
                        or cloud["folder_id"] != policy["folder_id"]
                        or not cloud.get("verified_at")
                    ):
                        raise ValueError("PDF_ARCHIVE_RECEIPT_MISMATCH")
                else:
                    cloud = archive.archive(path, digest)
                    atomic(archived_receipt, encoded(cloud))
                self.finish_body(
                    db,
                    job_id,
                    member_sha,
                    priority,
                    source,
                    json.loads(extracted_receipt.read_bytes()),
                    cloud,
                    path,
                )
                done += 1
                background += int(not priority)
            except Exception as exc:
                failed += 1
                reason = type(exc).__name__ + ":" + str(exc)
                wait = (
                    86400
                    if "BYTE_LIMIT" in reason or "NOT_PDF" in reason
                    else min(21600, 600 * (2 ** min(attempts, 5)))
                )
                db.execute(
                    "UPDATE bodies SET attempts=attempts+1,next_try=? WHERE job_id=?",
                    [time.time() + wait, job_id],
                )
                self.receipts.append(
                    {
                        "source": "cninfo-document",
                        "job_id": job_id,
                        "error": reason,
                        "recorded_at": now(),
                        "state": "pdf_archive_pending",
                    }
                )
                if "SOURCE_HTTP_429" in reason or "SOURCE_HELD" in reason:
                    alerts.append("DOCUMENT_SOURCE_HELD_BACKGROUND_PAUSED")
                    break
            db.commit()
        staged = list(stage.glob("*.pdf"))
        return {
            "pdfs_archived": done,
            "pdf_failures": failed,
            "pdf_downloaded_bytes": downloaded,
            "cloud_uploaded_bytes": archive.uploaded_bytes,
            "cloud_requests": archive.requests,
            "staged_bytes": sum(p.stat().st_size for p in staged),
            "staged_files": len(staged),
            "alerts": sorted(set(alerts)),
            "archive_scope": "ALL_NEW_FORWARD_DISCLOSURES_PRIORITY_FIRST",
            "pending": db.execute(
                "SELECT count(*) FROM bodies WHERE state='pdf_archive_pending'"
            ).fetchone()[0],
        }

    def run(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / "PIPELINE.json"
        binding = encoded(
            {
                "dataset_id": self.config["dataset_id"],
                "policy_version": self.config["policy_version"],
            }
        )
        if marker.exists() and marker.read_bytes() != binding:
            raise ValueError("STORE_BINDING_MISMATCH")
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError("STORE_NOT_DEDICATED")
            marker.write_bytes(binding)
        fd = os.open(self.root / ".capture.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_root.mkdir(parents=True)
            db = sqlite3.connect(self.root / "state.sqlite3")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS items("
                "id TEXT PRIMARY KEY,semantic TEXT,member_sha TEXT,first_seen TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS bodies(job_id TEXT PRIMARY KEY,url TEXT,title TEXT,"
                "priority INTEGER,state TEXT,attempts INTEGER,next_try REAL,member_sha TEXT)"
            )
            results = []
            for name, action in [
                ("news", self.news),
                ("announcements", self.announcements),
                ("bodies", self.bodies),
            ]:
                try:
                    results.append({"stage": name, "status": "CAPTURED", **action(db)})
                except Exception as exc:
                    results.append(
                        {
                            "stage": name,
                            "status": "PARTIAL_OR_BLOCKED",
                            "reason": type(exc).__name__ + ":" + str(exc),
                        }
                    )
            report = {
                "schema": "quanthus.p10.forward-capture-run.v1",
                "run_id": self.run_id,
                "recorded_at": now(),
                "dataset_id": self.config["dataset_id"],
                "code_revision": self.code_revision,
                "code_sha256": self.code_sha,
                "config_sha256": sha(encoded(self.config)),
                "requests": self.requests,
                "wire_bytes": self.bytes,
                "layer_bytes_written": self.layer_bytes,
                "elapsed_seconds": time.monotonic() - self.started,
                "stages": results,
                "source_receipts": self.receipts,
                "qualification": "RAW_TRIAL_ONLY",
                "Base2_6": "EXCLUDED",
                "consumer_admission": "NONE",
                "revision_detection_scope": (
                    "observed source metadata/body versions only; absence is not withdrawal"
                ),
                "python_runtime": sys.version,
            }
            target = self.run_root / "RESULT.json"
            target.write_bytes(encoded(report))
            (self.run_root / "SHA256").write_text(sha(target.read_bytes()) + "  RESULT.json\n")
            db.close()
            return {
                "run_id": self.run_id,
                "result_path": str(target),
                "result_sha256": sha(target.read_bytes()),
                "requests": self.requests,
                "wire_bytes": self.bytes,
                "stages": results,
            }
        finally:
            os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--expected-code-sha256", required=True)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if (
        not re.fullmatch(r"[0-9a-f]{40}", args.code_revision)
        or sha(Path(__file__).read_bytes()) != args.expected_code_sha256
    ):
        raise ValueError("FIXED_CODE_IDENTITY_MISMATCH")
    config = json.loads(args.config.read_bytes())
    while True:
        if sha(Path(__file__).read_bytes()) != args.expected_code_sha256:
            raise ValueError("RUNTIME_CODE_CHANGED")
        try:
            print(
                json.dumps(
                    Capture(args.root, config, args.code_revision).run(), ensure_ascii=False
                ),
                flush=True,
            )
        except BlockingIOError:
            print(json.dumps({"status": "SKIPPED_BUSY", "at": now()}), flush=True)
        if not args.loop or (args.root / "STOP").exists():
            break
        time.sleep(config["poll_seconds"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
