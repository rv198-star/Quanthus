"""Single-worker, append-only official notice fetch for an explicitly frozen job.

This is HTTP transport and exact pagination only. It cannot qualify actions,
infer old acquisition times, select research references or grant admission.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.p10_notice_census import NoticeCensus


def is_implementation_notice(title: str) -> bool:
    return bool(re.search(
        r"(权益分派|权益分配|利润分配|分红派息).*实施(?:的)?(?:更正|补充)?公告"
        r"(?:[（(].*[）)])?$", title
    ) or re.search(r"(?:年度|中期|半年度)权益分[派配]公告(?:[（(].*[）)])?$", title))


def write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("NOTICE_UNFROZEN_REDIRECT")


class FixedNoticeCollector:
    def __init__(self, root: Path) -> None:
        if root.resolve() != root or root.is_symlink():
            raise ValueError("NOTICE_JOB_PATH")
        self.root = root
        self.authorization = json.loads((root / "authorization.json").read_text())
        self.selection = json.loads((root / "metadata-selection.frozen.json").read_text())
        self.request = json.loads((root / "request.frozen.json").read_text())
        for name, field in [("request.frozen.json", "request_sha256"),
                            ("metadata-selection.frozen.json", "selection_sha256")]:
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != self.authorization[field]:
                raise ValueError("NOTICE_FROZEN_IDENTITY")
        targets = self.selection["targets"]
        if (len(targets) != 128 or len({t["code"] for t in targets}) != 128
                or self.selection["page_size"] != 30):
            raise ValueError("NOTICE_FIXED128_SELECTION")
        for target in targets:
            start, end = map(date.fromisoformat, target["metadata_window"])
            if (not date(2021, 1, 1) <= start <= end <= date(2021, 12, 31)
                    or target["exchange"] not in {"SSE", "SZSE"}
                    or not target["code"].isdigit() or len(target["code"]) != 6
                    or not target["org_id"]):
                raise ValueError("NOTICE_TARGET_SCOPE")
        self.targets = {t["code"]: t for t in targets}
        self.deadline = datetime.fromisoformat(
            self.authorization["effective_acquisition_deadline"]
        ).timestamp()
        self.ledger = root / "http-ledger.jsonl"
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )

    def append(self, value: dict[str, Any]) -> None:
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def accounting(self) -> tuple[int, int, list[dict[str, Any]]]:
        rows = ([json.loads(line) for line in self.ledger.read_text().splitlines()]
                if self.ledger.exists() else [])
        starts = {r["attempt"]: r for r in rows if r["event"] == "started"}
        ends = {r["attempt"]: r for r in rows if r["event"] == "finished"}
        if set(starts) != set(ends):
            raise ValueError("NOTICE_INTERRUPTED_ATTEMPT_REQUIRES_ACCOUNTING")
        return len(starts), sum(r["bytes"] for r in ends.values()), rows

    def guard(self, reserve: int) -> None:
        if time.time() >= self.deadline:
            raise ValueError("NOTICE_APPROVED_DEADLINE")
        total = 0
        for parent, dirs, files in os.walk(self.root, onerror=self._walk_error):
            for name in dirs + files:
                path = Path(parent) / name
                if path.is_symlink():
                    raise ValueError("NOTICE_JOB_SYMLINK")
                st = path.stat()
                total += max(st.st_size, st.st_blocks * 512)
        if total + reserve > self.authorization["maximum_new_output_bytes"]:
            raise ValueError("NOTICE_OUTPUT_CAP")
        stv = os.statvfs(self.root)
        if (stv.f_bavail * stv.f_frsize - reserve
                < self.authorization["minimum_free_bytes"] or stv.f_favail < 10000):
            raise ValueError("NOTICE_CAPACITY_FLOOR")

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise error

    def fetch(self, kind: str, code: str, *, page: int | None = None,
              body_url: str | None = None, notice_id: str | None = None
              ) -> tuple[bytes, dict[str, Any]]:
        if kind not in {"metadata", "body"} or code not in self.targets:
            raise ValueError("NOTICE_FETCH_SCOPE")
        target = self.targets[code]
        attempts, total, rows = self.accounting()
        cap = self.authorization
        if attempts >= cap["maximum_requests"] or total >= cap["maximum_response_bytes"]:
            raise ValueError("NOTICE_REQUEST_OR_BYTE_CAP")
        starts = [r for r in rows if r["event"] == "started"]
        if kind == "metadata" and sum(r["kind"] == kind for r in starts) >= cap[
            "maximum_metadata_requests"
        ]:
            raise ValueError("NOTICE_METADATA_CAP")
        if kind == "body" and len({r["notice_id"] for r in starts if r["kind"] == kind}
                                  | {notice_id}) > cap["maximum_unique_official_bodies"]:
            raise ValueError("NOTICE_BODY_CAP")
        max_bytes = 16 * 2**20 if kind == "body" else 4 * 2**20
        self.guard(max_bytes)
        form = None
        if kind == "metadata":
            if type(page) is not int or page < 1:
                raise ValueError("NOTICE_PAGE_SCOPE")
            url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
            form = dict(pageNum=str(page), pageSize="30", tabName="fulltext", searchkey="",
                        stock=code + "," + target["org_id"], secid="", plate="", category="",
                        trade="", seDate="~".join(target["metadata_window"]), sortName="time",
                        sortType="asc", isHLtitle="false",
                        column="sse" if target["exchange"] == "SSE" else "szse")
        else:
            if not body_url or not notice_id:
                raise ValueError("NOTICE_BODY_IDENTITY")
            url = body_url
        parsed = urllib.parse.urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.request[
            "source_hostnames_allowed"
        ] or parsed.username or parsed.password or parsed.port not in {443, None}):
            raise ValueError("NOTICE_URL_SCOPE")
        attempt = attempts + 1
        utc = datetime.now(timezone.utc).isoformat()
        request = dict(attempt=attempt, kind=kind, code=code, page=page, notice_id=notice_id,
                       url=url, form=form, UTC=utc)
        write_new(self.root / "requests" / f"request-{attempt:04d}.json", request)
        self.append(dict(event="started", **request))
        path = self.root / "raw" / f"{kind}-{code}-{notice_id or page}-{attempt:04d}.raw"
        part = path.with_name(path.name + ".part")
        content = bytearray()
        receipt = dict(event="finished", attempt=attempt, kind=kind, code=code, page=page,
                       notice_id=notice_id, acquisition_started_at=utc, TLS_verified=True)
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; Quanthus-P10-governance/1.0)",
                       "Referer": "https://www.cninfo.com.cn/"}
            if form is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            req = urllib.request.Request(
                url, data=urllib.parse.urlencode(form).encode() if form is not None else None,
                headers=headers, method="POST" if form is not None else "GET",
            )
            try:
                response = self.opener.open(req, timeout=min(30, max(1, self.deadline-time.time())))
            except urllib.error.HTTPError as error:
                response = error
            with response:
                with part.open("xb") as stream:
                    while True:
                        if time.time() >= self.deadline:
                            raise ValueError("NOTICE_APPROVED_DEADLINE")
                        remaining = min(max_bytes-len(content), cap["maximum_response_bytes"]
                                        - total-len(content))
                        if remaining <= 0:
                            raise ValueError("NOTICE_RESPONSE_CAP")
                        block = response.read(min(65536, remaining))
                        if not block:
                            break
                        stream.write(block)
                        content.extend(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                part.rename(path)
                if response.status != 200:
                    raise ValueError("NOTICE_HTTP_STATUS_" + str(response.status))
                receipt.update(status="downloaded", path=str(path), http_status=response.status,
                               sha256=hashlib.sha256(content).hexdigest())
        except Exception as error:
            receipt.update(status="failed", error_type=type(error).__name__, reason=str(error),
                           retained_path=str(path if path.exists() else part))
        receipt.update(bytes=len(content), acquired_at=datetime.now(timezone.utc).isoformat())
        self.append(receipt)
        time.sleep(min(.35, max(0, self.deadline-time.time())))
        return bytes(content), receipt

    def run_metadata(self) -> dict[str, Any]:
        results = []
        for target in self.selection["targets"]:
            dest = self.root / "evidence" / ("metadata-" + target["code"] + ".json")
            if dest.exists():
                raise ValueError("NOTICE_EXISTING_RESULT_REQUIRES_VERIFIED_RESUME")
            census = NoticeCensus(target["code"], target["org_id"],
                                  *map(date.fromisoformat, target["metadata_window"]))
            receipts = []
            status = "INCOMPLETE"
            try:
                while not census.complete:
                    raw, receipt = self.fetch("metadata", target["code"], page=census.pages+1)
                    receipts.append(receipt)
                    if receipt["status"] != "downloaded":
                        status = "BLOCKED_HTTP_OR_RESPONSE"
                        break
                    census.accept(census.pages+1, json.loads(raw))
                if census.complete:
                    status = "PASS_OFFICIAL_EXACT_METADATA_CENSUS"
            except ValueError as error:
                status = "BLOCKED:" + str(error)
            row = dict(**target, status=status, complete=census.complete,
                       accepted_pages=census.pages, physical_rows=census.physical_row_count,
                       exact_duplicate_rows=census.exact_duplicate_count,
                       unique_count=census.unique_count, receipts=receipts,
                       notices=list(census.notices), source_admission=False)
            write_new(dest, row)
            results.append(row)
            print(json.dumps(dict(processed=len(results), complete=sum(r["complete"] for r in
                              results), status=status, requests=self.accounting()[0])), flush=True)
            if any(word in status for word in ("DEADLINE", "CAP", "FLOOR", "INTERRUPTED")):
                break
        requests, count, _ = self.accounting()
        summary = dict(kind="p10-stage34-fixed128-official-metadata-census-v1",
                       UTC=datetime.now(timezone.utc).isoformat(), selected=128,
                       processed=len(results), complete=sum(r["complete"] for r in results),
                       status_counts=dict(Counter(r["status"] for r in results)),
                       requests=requests, response_bytes=count, source_admission=False)
        write_new(self.root / "evidence" / "metadata-summary.safe.json", summary)
        return summary

    def prepare_bodies(self) -> list[dict[str, Any]]:
        """Replay all frozen responses before freezing exact publisher adjuncts."""
        bodies = []
        screened = []
        for target in self.selection["targets"]:
            path = self.root / "evidence" / ("metadata-" + target["code"] + ".json")
            item = json.loads(path.read_text())
            if not item["complete"] or item["metadata_window"] != target["metadata_window"]:
                raise ValueError("NOTICE_INCOMPLETE_METADATA")
            census = NoticeCensus(target["code"], target["org_id"],
                                  *map(date.fromisoformat, target["metadata_window"]))
            for receipt in item["receipts"]:
                member = Path(receipt["path"])
                if member.resolve() != member or not member.is_relative_to(self.root / "raw"):
                    raise ValueError("NOTICE_METADATA_MEMBER_PATH")
                raw = member.read_bytes()
                if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
                    raise ValueError("NOTICE_METADATA_BYTES_CHANGED")
                census.accept(receipt["page"], json.loads(raw))
            if not census.complete or census.unique_count != item["unique_count"]:
                raise ValueError("NOTICE_METADATA_REPLAY")
            for notice in census.notices:
                title = re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", notice["announcementTitle"]))
                if not re.search(
                    r"权益分派|权益分配|利润分配|分红派息|资本公积|除权|除息|配股|送股|转增|换股|"
                    r"吸收合并|合并上市|分拆上市|证券简称|更名|上市公告|股份拆细|股份合并|终止上市",
                    title,
                ):
                    continue
                implementation = is_implementation_notice(title)
                row = dict(code=target["code"], notice_id=notice["announcementId"], title=title,
                           publication_at=datetime.fromtimestamp(
                               notice["announcementTime"]/1000, timezone.utc
                           ).isoformat(), adjunctUrl=notice["adjunctUrl"],
                           metadata_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           kind="IMPLEMENTATION" if implementation
                           else "POTENTIAL_ACTION_OR_IDENTITY")
                screened.append(row)
                if implementation:
                    url = urllib.parse.urljoin("https://static.cninfo.com.cn/", row["adjunctUrl"])
                    parsed = urllib.parse.urlparse(url)
                    if (parsed.hostname != "static.cninfo.com.cn" or parsed.scheme != "https"
                            or row["notice_id"] not in parsed.path):
                        raise ValueError("NOTICE_EXACT_ADJUNCT_IDENTITY")
                    bodies.append(dict(**row, url=url))
        keys = {(r["code"], r["notice_id"]) for r in bodies}
        if (len(keys) != len(bodies)
                or len(keys) > self.authorization["maximum_unique_official_bodies"]):
            raise ValueError("NOTICE_BODY_SELECTION_CAP")
        write_new(self.root / "evidence" / "action-title-dispositions.private.json", screened)
        write_new(self.root / "evidence" / "body-selection.frozen.json", bodies)
        return bodies

    def run_bodies(self) -> dict[str, Any]:
        bodies = self.prepare_bodies()
        records = []
        documents = []
        for body in bodies:
            raw, receipt = self.fetch("body", body["code"], body_url=body["url"],
                                      notice_id=body["notice_id"])
            records.append(receipt)
            if receipt["status"] == "downloaded" and raw.startswith(b"%PDF-"):
                path = self.root / "tmp" / ("notice-" + body["code"] + "-"
                                           + body["notice_id"] + ".txt")
                if path.exists():
                    raise ValueError("NOTICE_TEXT_EXISTS")
                try:
                    subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", receipt["path"],
                                    str(path)], check=True, timeout=20, capture_output=True)
                    text = path.read_bytes()
                    if len(text) > 4 * 2**20:
                        raise ValueError("NOTICE_TEXT_CAP")
                    documents.append(dict(**body, PDF_path=receipt["path"],
                                          PDF_sha256=receipt["sha256"], text_path=str(path),
                                          text_sha256=hashlib.sha256(text).hexdigest(),
                                          actual_new_acquired_at=receipt["acquired_at"]))
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    receipt["parser_error"] = type(error).__name__
            self.guard(0)
            print(json.dumps(dict(bodies_processed=len(records), downloaded=len(documents),
                                  requests=self.accounting()[0])), flush=True)
        result = dict(kind="p10-stage34-fixed128-body-fetch-v1", documents=documents,
                      attempts=records, source_admission=False, requests=self.accounting()[0],
                      response_bytes=self.accounting()[1])
        write_new(self.root / "evidence" / "body-results.private.json", result)
        return result
