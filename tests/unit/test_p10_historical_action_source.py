"""Exact offline source closure, literal units, correction dates and real clocks."""

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse import p10_historical_action_source as s


def seal(root, event):
    (root / "facts.json").write_text(json.dumps({"kind": s.VERSION, "events": [event]}))
    files = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": s.sha(p)}
        for p in sorted(root.iterdir())
        if p.name not in {"manifest.json", "SHA256SUMS"}
    ]
    m = {
        "kind": s.VERSION,
        "source_revision": "synthetic-reviewed-complete-plan",
        "source_information_cutoff": event["known_at"],
        "files": files,
        "historical_PIT_claim": False,
        "production_authorized": False,
        "exchange_reference_qualified": False,
        "consumer_admission": "NOT_GRANTED",
    }
    (root / "manifest.json").write_text(json.dumps(m))
    sha = s.sha(root / "manifest.json")
    (root / "SHA256SUMS").write_text(
        "".join(f"{r['sha256']}  {r['path']}\n" for r in files) + f"{sha}  manifest.json\n"
    )
    return sha


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    text = (
        "证券代码：123456\n总股本100股，向全体股东每10股派2.50元现金（含税），"
        "每10股转增5.00股。\n登记日2012年5月30日\n除权日2012年5月31日\n"
    )
    (root / "pdf-1.txt").write_text(text)
    (root / "pdf.bin").write_bytes(b"%PDF-synthetic-not-market-data")
    detail = "var v3_cninfo = 'https://static.cninfo.com.cn/';"
    (root / "detail.bin").write_text(detail)
    metadata = {
        "announcements": [
            {"announcementId": "1", "secCode": "123456", "adjunctUrl": "synthetic.PDF"}
        ]
    }
    (root / "metadata.bin").write_text(json.dumps(metadata))
    link = {
        "url": "https://static.cninfo.com.cn/synthetic.PDF",
        "metadata_sha256": s.sha(root / "metadata.bin"),
        "detail_sha256": s.sha(root / "detail.bin"),
    }
    (root / "publisher-link-1.json").write_text(json.dumps(link))

    def fact(value, quote):
        start = text.index(quote)
        end = start + len(quote)
        return {
            "value": value,
            "evidence": {
                "notice_id": "1",
                "start": start,
                "end": end,
                "quote": quote,
                "text_sha256": s.sha(root / "pdf-1.txt"),
                "utf8_byte_start": len(text[:start].encode()),
                "utf8_byte_end": len(text[:end].encode()),
            },
        }

    plan = text.splitlines()[1]
    terms = {
        "record_date": fact("2012-05-30", "登记日2012年5月30日"),
        "ex_date": fact("2012-05-31", "除权日2012年5月31日"),
        "cash_per_ten_gross": fact("2.50", plan),
        "bonus_per_ten": fact("0", plan),
        "transfer_per_ten": fact("5.00", plan),
    }
    terms.update(
        {
            n: {"value": None, "unknown": True}
            for n in ("cash_payment_date", "share_credit_date", "share_listing_date")
        }
    )
    event = {
        "security_code": "123456",
        "facts": terms,
        "documents": [
            {
                "notice_id": "1",
                "metadata_member": "metadata.bin",
                "detail_member": "detail.bin",
                "pdf_member": "pdf.bin",
                "pdf_sha256": s.sha(root / "pdf.bin"),
                "text_member": "pdf-1.txt",
                "acquired_at": "2026-10-24T16:00:00+00:00",
            }
        ],
        "known_at": "2026-10-24T16:00:00+00:00",
        "historical_known_at": None,
    }
    return root, event


def test_exact_literal_per_ten_preserves_unknown_and_real_observation(source):
    root, event = source
    (result,) = s.load_source(root, seal(root, event))
    assert result.cash_per_share == Decimal("0.25")
    assert result.bonus_share_ratio == Decimal("0.50")
    assert result.share_credit_date is None
    assert result.acquired_at == datetime(2026, 10, 24, 16, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "mutation",
    [
        "cash",
        "bonus",
        "date",
        "quote",
        "byte-offset",
        "security",
        "clock",
        "historical-clock",
        "publisher",
    ],
)
def test_fully_resealed_source_rejects_semantic_or_clock_or_lineage_forgery(source, mutation):
    root, original = source
    event = copy.deepcopy(original)
    if mutation == "cash":
        event["facts"]["cash_per_ten_gross"]["value"] = "2.51"
    elif mutation == "bonus":
        event["facts"]["bonus_per_ten"]["value"] = "5.00"
    elif mutation == "date":
        event["facts"]["record_date"]["value"] = "2011-05-30"
    elif mutation == "quote":
        event["facts"]["record_date"]["evidence"]["quote"] += "altered"
    elif mutation == "byte-offset":
        event["facts"]["ex_date"]["evidence"]["utf8_byte_start"] += 1
    elif mutation == "security":
        event["security_code"] = "123457"
    elif mutation == "clock":
        event["known_at"] = "2012-05-29T16:00:00+00:00"
    elif mutation == "historical-clock":
        event["historical_known_at"] = "2012-05-29T16:00:00+00:00"
    else:
        p = root / "publisher-link-1.json"
        link = json.loads(p.read_text())
        link["url"] = "https://unapproved.example/synthetic.PDF"
        p.write_text(json.dumps(link))
    with pytest.raises(ValueError, match="ACTION_SOURCE_"):
        s.load_source(root, seal(root, event))


def test_changed_bytes_extra_file_and_symlink_rejected(source):
    root, event = source
    sha = seal(root, event)
    (root / "pdf.bin").write_bytes(b"%PDF-corrupted")
    with pytest.raises(ValueError, match="MEMBER_CHANGED"):
        s.load_source(root, sha)
    (root / "extra.txt").write_text("unlisted")
    (root / "link").symlink_to(root / "extra.txt")
    with pytest.raises(ValueError, match="PHYSICAL_SET"):
        s.load_source(root, sha)
