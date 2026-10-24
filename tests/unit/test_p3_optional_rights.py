"""Optional rights are paid elections, never free shares or input-guard exemptions."""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_optional_rights as p
from quanthus.pipelines.securities_warehouse import p10_rights_issue_source as s

REPOSITORY = Path(__file__).resolve().parents[2]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def seal(root, event, rows):
    """Synthetic complete resealing allows tests to reach semantic checks."""
    (root / "rights-event-source-facts.json").write_text(json.dumps(event, ensure_ascii=False))
    (root / "acquisition-state.json").write_text(
        json.dumps({"requests": rows, "response_bytes": 30})
    )
    members = [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
        for p in sorted(root.iterdir())
        if p.name not in {"source-facts-manifest.json", "SHA256SUMS"}
    ]
    identity = sha(json.dumps(members, sort_keys=True, separators=(",", ":")).encode())
    m = {
        "kind": "p10-exact-rights-official-source-bundle-v1",
        "files": members,
        "content_sha256": identity,
        "source_revision": "p10-557-000901-rights-" + identity[:16],
        "consumer_admission": "NOT_GRANTED",
        "source_information_cutoff": event[
            "earliest_source_information_cutoff_for_this_new_bundle"
        ],
    }
    raw = json.dumps(m, ensure_ascii=False).encode()
    (root / "source-facts-manifest.json").write_bytes(raw)
    digest = sha(raw)
    (root / "SHA256SUMS").write_text(
        "".join(f"{p['sha256']}  {p['path']}\n" for p in members)
        + f"{digest}  source-facts-manifest.json\n"
    )
    return digest


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    text = "正文：synthetic source terms；end"
    quote = "synthetic source terms"
    rows = []
    for aid in ["A", "B", "C"]:
        pdf = root / f"{aid}.pdf"
        pdf.write_bytes(b"%PDF-synthetic-evidence")
        (root / f"pdf-{aid}.txt").write_text(text)
        rows.append(
            {
                "label": f"pdf-{aid}",
                "body_path": str(pdf),
                "status": 200,
                "sha256": sha(pdf.read_bytes()),
                "acquired_at": "2026-10-24T12:00:00+00:00",
            }
        )
    proof = {
        "notice_id": "A",
        "pdf_sha256": rows[0]["sha256"],
        "text_sha256": sha(text.encode()),
        "start": 3,
        "end": 3 + len(quote),
        "quote": quote,
        "utf8_byte_start": len(text[:3].encode()),
        "utf8_byte_end": len(text[: 3 + len(quote)].encode()),
    }
    values = {
        "record_date": "2014-10-15",
        "subscription_window": ["2014-10-16", "2014-10-22"],
        "ex_right_date": "2014-10-24",
        "resumption_date": "2014-10-24",
        "new_share_listing_date": "2014-11-04",
        "maximum_subscription_ratio": "0.30000000000",
        "subscription_price": "2.12500000000",
        "shares_before": 1000,
        "actual_issued_shares": 290,
        "shares_after": 1290,
        "unpaid_after_deadline": "subscription-rights-waived",
        "new_share_account_credit_instant": None,
        "exact_historical_publication_instants": None,
        "exchange_ex_right_formula_and_tick_rule": None,
    }
    facts = {
        k: {"value": v, **({"evidence": proof.copy()} if v is not None else {})}
        for k, v in values.items()
    }
    event = {
        "kind": "p10-official-rights-issue-source-facts-v1",
        "event_type": "optional-paid-rights-issue",
        "source_type": "official-CNINFO-issuer-publication",
        "consumer_admission": "NOT_GRANTED",
        "security_code": "000901",
        "source_prices_modified": False,
        "original_frozen_cutoff_rewritten": False,
        "acquired_at_by_notice": dict.fromkeys(["A", "B", "C"], rows[0]["acquired_at"]),
        "earliest_source_information_cutoff_for_this_new_bundle": rows[0]["acquired_at"],
        "facts": facts,
    }
    return root, event, rows


def test_local_source_proof_and_precise_terms_do_not_infer_account_credit(source):
    root, event, rows = source
    facts = s.load_rights_source(root, seal(root, event, rows))
    assert str(facts.maximum_subscription_ratio) == "0.30000000000"
    assert str(facts.subscription_price) == "2.12500000000"
    assert (
        Decimal(facts.actual_issued_shares) / facts.shares_before
        != facts.maximum_subscription_ratio
    )
    assert facts.listing_date == date(2014, 11, 4)
    assert facts.source_information_cutoff.year == 2026


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("quote", "QUOTE_CLOSURE"),
        ("offset", "QUOTE_CLOSURE"),
        ("byte-offset", "QUOTE_CLOSURE"),
        ("backdate", "CLOCK"),
        ("cash-action", "SCOPE"),
        ("inferred-credit", "UNSUPPORTED_INFERENCE"),
        ("inferred-time", "UNSUPPORTED_INFERENCE"),
        ("inferred-formula", "UNSUPPORTED_INFERENCE"),
        ("shares", "SHARE_CLOSURE"),
        ("boolean-shares", "SHARE_CLOSURE"),
        ("NaN", "DECIMAL"),
        ("float", "DECIMAL"),
        ("date-order", "BUSINESS_DATES"),
        ("resumption", "RESUMPTION"),
        ("code", "SECURITY"),
    ],
)
def test_resealed_false_claims_still_fail(source, mutation, error):
    root, event, rows = source
    f = event["facts"]
    if mutation == "quote":
        f["record_date"]["evidence"]["quote"] = "not in body"
    if mutation == "offset":
        f["record_date"]["evidence"]["start"] = -1
    if mutation == "byte-offset":
        f["record_date"]["evidence"]["utf8_byte_start"] = 3
    if mutation == "backdate":
        event["earliest_source_information_cutoff_for_this_new_bundle"] = (
            "2014-10-13T00:00:00+00:00"
        )
    if mutation == "cash-action":
        event["event_type"] = "cash-dividend"
    if mutation == "inferred-credit":
        f["new_share_account_credit_instant"]["value"] = "2014-11-04T00:00:00+08:00"
    if mutation == "inferred-time":
        f["exact_historical_publication_instants"]["value"] = "2014-10-13T00:00:00+08:00"
    if mutation == "inferred-formula":
        f["exchange_ex_right_formula_and_tick_rule"]["value"] = "guessed"
    if mutation == "shares":
        f["shares_after"]["value"] = 1291
    if mutation == "boolean-shares":
        f["shares_before"]["value"] = True
    if mutation == "NaN":
        f["subscription_price"]["value"] = "NaN"
    if mutation == "float":
        f["maximum_subscription_ratio"]["value"] = 0.3
    if mutation == "date-order":
        f["ex_right_date"]["value"] = "2014-10-16"
    if mutation == "resumption":
        f["resumption_date"]["value"] = "2014-10-25"
    if mutation == "code":
        event["security_code"] = "SZ000901"
    # Inferred values intentionally have no proof: a fake claim cannot pass by omission.
    for v in f.values():
        if v["value"] is not None and "evidence" not in v:
            v["evidence"] = f["record_date"]["evidence"].copy()
    with pytest.raises(ValueError, match=error):
        s.load_rights_source(root, seal(root, event, rows))


@pytest.mark.parametrize(
    "mutation", ["byte", "extra", "missing", "symlink", "wrong-manifest", "checksums"]
)
def test_physical_mutations_fail_closed(source, tmp_path, mutation):
    root, event, rows = source
    digest = seal(root, event, rows)
    if mutation == "byte":
        (root / "A.pdf").write_bytes(b"%PDF-corrupt")
    if mutation == "extra":
        (root / "unlisted").write_text("extra")
    if mutation == "missing":
        (root / "A.pdf").unlink()
    if mutation == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("external")
        (root / "A.pdf").unlink()
        (root / "A.pdf").symlink_to(outside)
    if mutation == "wrong-manifest":
        digest = "0" * 64
    if mutation == "checksums":
        (root / "SHA256SUMS").write_text("forged")
    with pytest.raises((ValueError, FileNotFoundError)):
        s.load_rights_source(root, digest)


def test_no_subscription_preserves_actual_dilution_and_never_opens_input_gate(source):
    root, event, rows = source
    facts = s.load_rights_source(root, seal(root, event, rows))
    results = [
        p.no_subscription_disposition(
            REPOSITORY,
            facts,
            arm=arm,
            market_date=date(2014, 10, 24),
            source_information_cutoff=facts.source_information_cutoff,
        )
        for arm in ["B", "T", "TCH"]
    ]
    assert all(r["status"] == "WAIVED_AFTER_DEADLINE" for r in results)
    assert all(
        r["subscription_cash_delta"] == "0" and r["subscription_share_delta"] == 0 for r in results
    )
    assert all(r["old_share_mark"] == "RETAIN_ACTUAL_OBSERVED_UNCOMPENSATED" for r in results)
    assert all(
        r["consumer_admission"] == r["price_limit_qualification"] == "NOT_GRANTED_BY_THIS_RECEIPT"
        for r in results
    )
    assert [{k: v for k, v in r.items() if k != "arm"} for r in results].count(
        {k: v for k, v in results[0].items() if k != "arm"}
    ) == 3


@pytest.mark.parametrize(
    "day,status",
    [
        ("2014-10-15", "NO_SUBSCRIPTION_ELECTED"),
        ("2014-10-22", "NO_SUBSCRIPTION_ELECTED"),
        ("2014-10-23", "WAIVED_AFTER_DEADLINE"),
        ("2014-11-04", "WAIVED_AFTER_DEADLINE"),
    ],
)
def test_do_not_infer_intraday_deadline_or_credit(source, day, status):
    root, event, rows = source
    f = s.load_rights_source(root, seal(root, event, rows))
    r = p.no_subscription_disposition(
        REPOSITORY,
        f,
        arm="B",
        market_date=date.fromisoformat(day),
        source_information_cutoff=f.source_information_cutoff,
    )
    assert r["status"] == status and r["historical_publication_instant"] is None
    assert r["subscription_share_delta"] == 0


@pytest.mark.parametrize(
    "arm,day,cutoff,error",
    [
        ("TR", "2014-10-24", "2026-10-24T12:00:00+00:00", "SCOPE"),
        ("B", "2022-01-04", "2026-10-24T12:00:00+00:00", "SCOPE"),
        ("B", "2014-10-24", "2014-10-24T12:00:00+00:00", "SOURCE_NOT_AVAILABLE"),
        ("B", "2014-10-24", "2026-10-24T12:00:00", "SOURCE_NOT_AVAILABLE"),
        ("B", "2014-10-14", "2026-10-24T12:00:00+00:00", "EVENT_NOT_ACTIVE"),
    ],
)
def test_unavailable_clocks_and_unapproved_scope_are_rejected(source, arm, day, cutoff, error):
    root, event, rows = source
    facts = s.load_rights_source(root, seal(root, event, rows))
    with pytest.raises(ValueError, match=error):
        p.no_subscription_disposition(
            REPOSITORY,
            facts,
            arm=arm,
            market_date=date.fromisoformat(day),
            source_information_cutoff=datetime.fromisoformat(cutoff),
        )


def test_policy_mutation_does_not_silently_allow_subscription(source, tmp_path):
    root, event, rows = source
    f = s.load_rights_source(root, seal(root, event, rows))
    policy = json.loads((REPOSITORY / p.POLICY).read_text())
    policy["subscription_share_credit"] = True
    path = tmp_path / p.POLICY
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="CONSUMER_POLICY"):
        p.no_subscription_disposition(
            tmp_path,
            f,
            arm="B",
            market_date=date(2014, 10, 24),
            source_information_cutoff=datetime(2026, 10, 24, 12, tzinfo=timezone.utc),
        )
