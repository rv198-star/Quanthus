from __future__ import annotations

import copy
import gzip
import json
import sqlite3
from pathlib import Path
from urllib.error import HTTPError

import pytest

from quanthus.forward_news_capture import (
    Capture,
    encoded,
    limited_markdown,
    normalize_announcement,
    normalize_news,
    parse_entity,
    source_clock,
)


def cfg():
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "config/pipelines/p10-forward-news-capture.v1.json").read_bytes())
    config["min_host_interval_seconds"] = 0
    config["pdf_per_run"] = 0
    config["ann_partitions"] = [{"column": "szse", "plate": "sz"}]
    return config


def news():
    return {
        "id": 123,
        "rich_text": "【公告】<b>英伟达</b>消息",
        "create_time": "2026-11-24 05:10:15",
        "ext": '{"stocks":[{"symbol":"sz300308"}]}',
    }


def ann():
    return {
        "announcementId": "456",
        "secCode": "300308",
        "secName": "source-name",
        "announcementTitle": "业绩预告",
        "announcementTime": 1795449600000,
        "adjunctUrl": "finalpage/2026-11-24/456.PDF",
    }


def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE items(id TEXT PRIMARY KEY,semantic TEXT,member_sha TEXT,first_seen TEXT)"
    )
    conn.execute(
        "CREATE TABLE bodies(job_id TEXT PRIMARY KEY,url TEXT,title TEXT,"
        "priority INTEGER,state TEXT,"
        "attempts INTEGER,next_try REAL,member_sha TEXT)"
    )
    return conn


def evidence(stamp="2026-11-24T05:12:05+08:00"):
    return {"response_completed_at": stamp, "raw": {"sha256": "a" * 64}}


def test_gzip_response_is_preserved_but_safely_decoded():
    raw = gzip.compress(encoded({"中文": [1, 2]}))
    assert parse_entity(raw, 1000) == {"中文": [1, 2]}
    with pytest.raises(ValueError, match="DECODED_RESPONSE_LIMIT"):
        parse_entity(gzip.compress(b" " * 10000), 100)


@pytest.mark.parametrize("value", [None, True, [], "garbage"])
def test_unknown_source_clock_is_not_filled_with_current_time(value):
    clock = source_clock(value, epoch_ms=True)
    assert clock["published_at"] is None
    assert clock["reason"]


def test_midnight_epoch_is_date_only_not_precise_publisher_clock():
    clock = source_clock(1795449600000, epoch_ms=True)
    assert clock["reported_date"] == "2026-11-24"
    assert clock["published_at"] is None
    assert clock["precision"] == "day"


def test_news_timezone_requires_source_evidence_and_original_entities_survive():
    item = news()
    assert source_clock(item["create_time"])["published_at"] is None
    normalized = normalize_news(item, "Tue Nov 24 05:12:04 +0800 2026")
    assert normalized["source_publication"]["published_at"] == "2026-11-24T05:10:15+08:00"
    assert normalized["source_entities_raw"] == [{"symbol": "sz300308"}]
    assert normalized["security_ids"] is None
    assert normalized["source_rich_text"] == item["rich_text"]


def test_announcement_record_and_document_ids_do_not_invent_security_identity():
    item = normalize_announcement(ann())
    assert item["source_record_id"] == "456@300308"
    assert item["source_document_id"] == "456"
    assert item["security_ids"] is None
    assert item["priority_body"]
    assert item["document_sha256"] is None


def test_markdown_utf8_bound_and_explicit_truncation():
    md, truncated = limited_markdown(
        "汉字\n" * 20000, title="公告", url="https://example.invalid", document_sha="a" * 64
    )
    assert truncated and len(md) < 32768
    assert "TRUNCATED" in md.decode("utf-8")


def test_content_dedup_revision_chain_and_original_first_seen(tmp_path):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    conn = db()
    payload = normalize_news(news(), "+0800")
    assert worker.item(conn, payload, evidence())
    assert not worker.item(conn, copy.deepcopy(payload), evidence("2026-11-24T05:17:05+08:00"))
    changed = copy.deepcopy(payload)
    changed["body"] += "修订"
    assert worker.item(conn, changed, evidence("2026-11-24T05:18:05+08:00"))
    rows = [json.loads(p.read_bytes()) for p in (tmp_path / "objects" / "case-input").glob("*/*")]
    assert len(rows) == 2
    revised = next(r for r in rows if r["previous_member_sha256"])
    assert revised["first_seen_at"] == "2026-11-24T05:12:05+08:00"
    assert revised["version_first_seen_at"] == "2026-11-24T05:18:05+08:00"


def test_later_revision_never_backfills_frozen_case_input(tmp_path):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    conn = db()
    payload = normalize_news(news(), "+0800")
    worker.item(conn, payload, evidence())
    payload["body"] += "later revision"
    worker.item(conn, payload, evidence("2026-11-24T10:00:00+08:00"))
    inputs = list((tmp_path / "objects" / "case-input").glob("*/*"))
    outputs = list((tmp_path / "objects" / "case-result-capture").glob("*/*"))
    assert len(inputs) == len(outputs) == 1
    assert json.loads(outputs[0].read_bytes())["case_role"] == "POST_CUTOFF_RESULT_CAPTURE"


def test_policy_change_is_a_new_semantic_input_closure(tmp_path):
    conn = db()
    config = cfg()
    a = Capture(tmp_path, config, "b" * 40)
    payload = normalize_news(news(), "+0800")
    assert a.item(conn, payload, evidence())
    config = copy.deepcopy(config)
    config["policy_version"] = "test-revised-policy"
    assert Capture(tmp_path, config, "b" * 40).item(conn, payload, evidence())


def test_source_unavailability_is_isolated_and_no_source_ready_is_signed(tmp_path, monkeypatch):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    monkeypatch.setattr(worker, "news", lambda conn: {"rows_seen": 2})

    def blocked(conn):
        raise ValueError("SOURCE_HTTP_403")

    monkeypatch.setattr(worker, "announcements", blocked)
    monkeypatch.setattr(worker, "bodies", lambda conn: {"pending": 0})
    report = worker.run()
    result = json.loads(Path(report["result_path"]).read_bytes())
    assert result["stages"][0]["status"] == "CAPTURED"
    assert result["stages"][1]["status"] == "PARTIAL_OR_BLOCKED"
    assert result["consumer_admission"] == "NONE"


def test_403_is_persistently_held_without_access_workaround(tmp_path, monkeypatch):
    worker = Capture(tmp_path, cfg(), "b" * 40)

    def denied(request, **kwargs):
        raise HTTPError(request.full_url, 403, "denied", {}, None)

    monkeypatch.setattr("quanthus.forward_news_capture.urlopen", denied)
    with pytest.raises(ValueError, match="SOURCE_HTTP_403"):
        worker.fetch("test", "https://www.cninfo.com.cn/new/hisAnnouncement/query")
    other = Capture(tmp_path, cfg(), "b" * 40)
    with pytest.raises(ValueError, match="SOURCE_HELD"):
        other.fetch("test", "https://www.cninfo.com.cn/new/hisAnnouncement/query")


def test_disallowed_document_host_is_rejected_before_network(tmp_path):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    with pytest.raises(ValueError, match="SOURCE_HOST_NOT_ALLOWED"):
        worker.fetch("test", "https://example.invalid/private")
    assert worker.requests == 0


def test_capture_root_must_not_be_an_unrelated_existing_store(tmp_path):
    (tmp_path / "unrelated").write_text("keep")
    with pytest.raises(ValueError, match="STORE_NOT_DEDICATED"):
        Capture(tmp_path, cfg(), "b" * 40).run()
    assert (tmp_path / "unrelated").read_text() == "keep"


def pdf_worker(tmp_path, monkeypatch):
    config = cfg()
    config["pdf_per_run"] = 6
    worker = Capture(tmp_path, config, "b" * 40)
    conn = db()
    worker.item(conn, normalize_announcement(ann()), evidence())
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        return b"%PDF-test-original", evidence()

    monkeypatch.setattr(worker, "fetch", fetch)
    monkeypatch.setattr(
        worker, "extract_document", lambda *a, **kw: {"state": "EXTRACTED", "markdown": None}
    )
    return worker, conn, calls


def cloud_receipt(worker, path, digest):
    return {
        "sha256": digest,
        "bytes": path.stat().st_size,
        "folder_id": worker.config["pdf_storage"]["folder_id"],
        "file_id": "remote-object",
        "verified_at": "2026-11-24T06:00:00+08:00",
        "verification": "REMOTE_FULL_READBACK_SHA256_AND_SIZE",
    }


def test_failed_upload_retains_staged_pdf_and_retry_never_redownloads(tmp_path, monkeypatch):
    worker, conn, calls = pdf_worker(tmp_path, monkeypatch)

    def fail(self, path, digest):
        raise ValueError("PDF_ARCHIVE_HTTP_503")

    monkeypatch.setattr("quanthus.forward_news_capture.DrivePdfArchive.archive", fail)
    assert worker.bodies(conn)["pdf_failures"] == 1
    assert len(list((tmp_path / "pdf-staging").glob("*.pdf"))) == 1
    assert not (tmp_path / "objects" / "pdf-original").exists()
    conn.execute("UPDATE bodies SET next_try=0")
    monkeypatch.setattr(
        "quanthus.forward_news_capture.DrivePdfArchive.archive",
        lambda self, path, digest: cloud_receipt(worker, path, digest),
    )
    result = worker.bodies(conn)
    assert result["pdfs_archived"] == 1 and result["staged_files"] == 0
    assert len(calls) == 1
    assert conn.execute("SELECT state FROM bodies").fetchone()[0] == "archived"
    assert list((tmp_path / "pdf-jobs").glob("*/ARCHIVED.json"))


def test_wrong_remote_readback_never_deletes_local_original(tmp_path, monkeypatch):
    worker, conn, calls = pdf_worker(tmp_path, monkeypatch)

    def wrong(self, path, digest):
        return {**cloud_receipt(worker, path, digest), "sha256": "0" * 64}

    monkeypatch.setattr("quanthus.forward_news_capture.DrivePdfArchive.archive", wrong)
    result = worker.bodies(conn)
    assert result["pdf_failures"] == 1 and result["staged_files"] == 1
    assert result["pending"] == 1


def test_after_cloud_receipt_crash_retry_needs_no_local_pdf_or_redownload(tmp_path, monkeypatch):
    worker, conn, calls = pdf_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "quanthus.forward_news_capture.DrivePdfArchive.archive",
        lambda self, path, digest: cloud_receipt(worker, path, digest),
    )
    worker.bodies(conn)
    conn.execute("UPDATE bodies SET state='pdf_archive_pending',next_try=0")

    def unavailable(*args, **kwargs):
        raise AssertionError("retry must reuse bound verified receipt")

    monkeypatch.setattr(worker, "fetch", unavailable)
    assert worker.bodies(conn)["pdfs_archived"] == 1
    assert len(calls) == 1


def test_full_staging_pauses_pdf_but_preserves_metadata_queue(tmp_path, monkeypatch):
    worker, conn, calls = pdf_worker(tmp_path, monkeypatch)
    worker.config["pdf_storage"]["staging_max_bytes"] = 1
    result = worker.bodies(conn)
    assert result["alerts"] == ["PDF_STAGING_FULL_DOWNLOAD_PAUSED"]
    assert result["pending"] == 1 and not calls
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_background_archive_is_queued_and_skips_text_extraction(tmp_path, monkeypatch):
    worker, conn, calls = pdf_worker(tmp_path, monkeypatch)
    item = ann()
    item["announcementId"] = "other"
    item["announcementTitle"] = "常规会议通知"
    worker.item(conn, normalize_announcement(item), evidence())
    conn.execute("DELETE FROM bodies WHERE priority=1")

    def no_text(*args, **kwargs):
        raise AssertionError("background must not extract text")

    monkeypatch.setattr(worker, "extract_document", no_text)
    monkeypatch.setattr(
        "quanthus.forward_news_capture.DrivePdfArchive.archive",
        lambda self, path, digest: cloud_receipt(worker, path, digest),
    )
    assert worker.bodies(conn)["pdfs_archived"] == 1
    entry = json.loads(next((tmp_path / "pdf-jobs").glob("*/EXTRACTION.json")).read_bytes())
    assert entry["state"] == "BACKGROUND_ARCHIVE_ONLY"


def test_source_revision_preserves_both_pdf_jobs(tmp_path):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    conn = db()
    item = normalize_announcement(ann())
    worker.item(conn, item, evidence())
    item["title"] += "修订"
    worker.item(conn, item, evidence("2026-11-24T06:00:00+08:00"))
    assert conn.execute("SELECT count(*) FROM bodies").fetchone()[0] == 2
    assert conn.execute("SELECT count(DISTINCT member_sha) FROM bodies").fetchone()[0] == 2


def test_chinese_paragraphs_and_repeated_headers_are_locally_normalized():
    from quanthus.forward_news_capture import clean_chinese_pages

    pages = [
        "固定公司页眉\n\n这是一段中文\n在同一段内继续。\n\n1",
        "固定公司页眉\n\n第二段内容。\n\n2",
        "固定公司页眉\n\n第三段内容。\n\n3",
    ]
    text = clean_chinese_pages(pages)
    assert "固定公司页眉" not in text
    assert "这是一段中文在同一段内继续。" in text


def test_drive_sha_readback_failure_is_explicit(tmp_path, monkeypatch):
    from quanthus.p10_news_pdf_archive import DrivePdfArchive

    worker = DrivePdfArchive(cfg()["pdf_storage"])
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-test")
    from quanthus.forward_news_capture import sha

    digest = sha(pdf.read_bytes())
    monkeypatch.setattr(worker, "folder", lambda *a: "folder-id")
    monkeypatch.setattr(worker, "children", lambda *a: [{"id": "remote-id"}])
    monkeypatch.setattr(
        worker,
        "request",
        lambda url, **kw: (
            b"bad-bytes" if "alt=media" in url else encoded({"size": pdf.stat().st_size})
        ),
    )
    with pytest.raises(ValueError, match="READBACK_SHA_MISMATCH"):
        worker.archive(pdf, digest)
    assert pdf.exists()


def test_text_cache_is_bound_to_pdf_and_extractor_version(tmp_path, monkeypatch):
    from quanthus.forward_news_capture import sha
    from quanthus.p10_news_pdf_archive import atomic

    worker = Capture(tmp_path, cfg(), "b" * 40)
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF")
    digest = sha(path.read_bytes())
    version = worker.config["text_extractor_version"]
    key = sha(encoded({"pdf_sha256": digest, "extractor_version": version}))
    cached = {
        "pdf_sha256": digest,
        "extractor_version": version,
        "state": "EXTRACTED",
        "markdown": None,
    }
    atomic(tmp_path / "extraction-cache" / (key + ".json"), encoded(cached))
    assert (
        worker.extract_document(path, title="new title", url="other subject", digest=digest)
        == cached
    )
    worker.config["text_extractor_version"] = "different-version"
    worker.config["pdftotext"] = str(tmp_path / "unavailable")
    assert (
        worker.extract_document(path, title="", url="", digest=digest)["state"]
        == "TEXT_EXTRACTOR_UNAVAILABLE"
    )


def test_ocr_output_cannot_replace_a_page_that_passed_text_path(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    worker = Capture(tmp_path, cfg(), "b" * 40)
    worker.config["pdftotext"] = str(tmp_path / "pdftotext")
    Path(worker.config["pdftotext"]).write_text("test marker")

    def command(args, **kwargs):
        if "-v" in args:
            return CompletedProcess(args, 0, stdout=b"", stderr=b"pdftotext version 22.02.0\n")
        Path(args[-1]).write_text("A sufficiently long real text page that passed.\f")
        return CompletedProcess(args, 0)

    monkeypatch.setattr("quanthus.forward_news_capture.subprocess.run", command)
    monkeypatch.setattr(
        worker,
        "ocr_failed_pages",
        lambda *args: {
            "state": "bad",
            "pages": [{"page": 1, "state": "OCR_TEXT", "text": "unrequested"}],
        },
    )
    result = worker.extract_document(tmp_path / "sample.pdf", title="", url="", digest="a" * 64)
    assert result["state"] == "TEXT_EXTRACTION_FAILED"


def test_exact_cutoff_is_result_side_not_frozen_input(tmp_path):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    worker.item(db(), normalize_news(news(), "+0800"), evidence("2026-11-24T09:15:00+08:00"))
    assert not (tmp_path / "objects" / "case-input").exists()
    assert list((tmp_path / "objects" / "case-result-capture").glob("*/*"))


def test_repeated_provider_page_is_bounded_partial_and_next_run_incremental(tmp_path, monkeypatch):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    conn = db()
    calls = []

    def page(*args, **kwargs):
        calls.append(args)
        return encoded(
            {"announcements": [ann()], "totalAnnouncement": 1274, "hasMore": True}
        ), evidence()

    monkeypatch.setattr(worker, "fetch", page)
    result = worker.announcements(conn)
    assert len(calls) == 2 and result["unique_source_rows"] == 1
    assert result["pagination_blocker"] == "PROVIDER_REPEATED_PAGE"
    assert result["status"] == "PARTIAL_SOURCE_COVERAGE"
    assert result["provider_pagination_complete"] is False
    calls.clear()
    assert worker.announcements(conn)["scan_mode"] == "INCREMENTAL"
    assert len(calls) == 1


def test_distinct_provider_pages_complete_only_after_unique_census(tmp_path, monkeypatch):
    worker = Capture(tmp_path, cfg(), "b" * 40)
    conn = db()
    calls = []

    def page(*args, **kwargs):
        index = len(calls)
        calls.append(index)
        item = ann()
        item["announcementId"] = str(index)
        return encoded(
            {"announcements": [item], "totalAnnouncement": 2, "hasMore": index == 0}
        ), evidence()

    monkeypatch.setattr(worker, "fetch", page)
    result = worker.announcements(conn)
    assert result["provider_pagination_complete"] is True
    assert result["unique_source_rows"] == 2 and result["pagination_blocker"] is None


def test_cninfo_posts_use_explicit_form_and_ajax_protocol(tmp_path, monkeypatch):
    from urllib.parse import parse_qs

    config = cfg()
    worker = Capture(tmp_path, config, "b" * 40)
    seen = []

    class Reply:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b"{}"

    def opening(req, **kwargs):
        seen.append(req)
        return Reply()

    monkeypatch.setattr("quanthus.forward_news_capture.urlopen", opening)
    worker.fetch(
        "cninfo-announcements",
        "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        {"pageNum": 2, "pageSize": 30, "column": "szse", "plate": "sz"},
        post=True,
    )
    headers = {k.lower(): v for k, v in seen[0].headers.items()}
    assert headers["x-requested-with"] == "XMLHttpRequest"
    assert headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert parse_qs(seen[0].data.decode())["pageNum"] == ["2"]


def test_venue_failure_preserves_independent_partition_capture(tmp_path, monkeypatch):
    config = cfg()
    config["ann_partitions"] = [{"column": "szse", "plate": "sz"}, {"column": "sse", "plate": "sh"}]
    worker = Capture(tmp_path, config, "b" * 40)

    def venue(conn, partition):
        if partition["column"] == "szse":
            raise ValueError("SOURCE_HTTP_403")
        return {"column": "sse", "provider_pagination_complete": True, "unique_source_rows": 3}

    monkeypatch.setattr(worker, "announcements_partition", venue)
    result = worker.announcements(db())
    assert result["partitions"][0]["status"] == "PARTIAL_OR_BLOCKED"
    assert result["partitions"][1]["provider_pagination_complete"]
    assert result["unique_source_rows"] == 3 and not result["provider_pagination_complete"]
