"""The issuer ratio, exchange rounding and source clock have distinct authority."""

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from quanthus.pipelines.securities_warehouse import p10_rights_issue_reference as p
from quanthus.pipelines.securities_warehouse.p10_rights_issue_source import RightsIssueFacts

NOW = datetime(2026, 10, 24, 13, 20, tzinfo=timezone.utc)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def seal(root, facts, docs, state):
    (root / "rule-facts.json").write_text(json.dumps(facts, ensure_ascii=False))
    (root / "budget-state.json").write_text(json.dumps(state))
    members = [
        {"path": f.name, "bytes": f.stat().st_size, "sha256": sha(f.read_bytes())}
        for f in sorted(root.iterdir())
        if f.name not in {"manifest.json", "SHA256SUMS"}
    ]
    identity = sha(json.dumps(members, sort_keys=True, separators=(",", ":")).encode())
    m = {
        "kind": "p10-szse-exright-official-rule-bundle-v1",
        "source_revision": "p10-557-szse-rule-" + identity[:16],
        "content_sha256": identity,
        "files": members,
        "consumer_admission": "NOT_GRANTED",
        "documents": docs,
        "source_information_cutoff": NOW.isoformat(),
    }
    raw = json.dumps(m).encode()
    (root / "manifest.json").write_bytes(raw)
    digest = sha(raw)
    (root / "SHA256SUMS").write_text(
        "".join(f"{f['sha256']}  {f['path']}\n" for f in members) + digest + "  manifest.json\n"
    )
    return digest


@pytest.fixture
def rule_source(tmp_path):
    root = tmp_path / "official-rule"
    root.mkdir()
    text = "来源：synthetic rule text"
    docs = []
    for label in ("rule-201311", "rule-201512-successor"):
        pdf = b"%PDF-synthetic-rule"
        (root / (label + ".body")).write_bytes(pdf)
        (root / (label + ".txt")).write_text(text)
        docs.append(
            {
                "label": label,
                "url": "https://www.szse.cn/synthetic-test-only.pdf",
                "sha256": sha(pdf),
                "acquired_at": NOW.isoformat(),
            }
        )
    proof = {
        "document": docs[0]["label"],
        "url": docs[0]["url"],
        "pdf_sha256": docs[0]["sha256"],
        "text_sha256": sha(text.encode()),
        "start": 3,
        "end": len(text),
        "utf8_byte_start": len(text[:3].encode()),
        "utf8_byte_end": len(text.encode()),
        "quote": text[3:],
    }
    facts = {
        "kind": "p10-szse-exright-rule-facts-v1",
        "rule_revision": "SZSE-TRADING-2013-11",
        "qualified_event_date": "2014-10-24",
        "revision_effective_date_claim": "2013-11-30",
        "later_revision_dates_claimed": ["2015-01-09", "2015-12-04"],
        "A_share_tick": "0.01",
        "limit_rounding": "ROUND_HALF_UP_TO_TICK_AFTER_MULTIPLICATION",
        "reference_intermediate_rounding": (
            "NO_UNPROVED_PRE_ROUNDING; retain exact rational reference"
        ),
        "source_information_cutoff": NOW.isoformat(),
        "original_historical_acquisition_instant": None,
        "market_prices_modified": False,
        "per_decision_historical_PIT_claim": False,
        "clauses": {k: proof.copy() for k in p.CLAUSES},
    }
    state = {
        "status": "CLOSED_TWO_APPROVED_DOCUMENTS_COMPLETE",
        "request_count": 4,
        "response_bytes": 100,
    }
    return root, facts, docs, state


def test_offline_physical_rule_closure_keeps_real_new_cutoff(rule_source):
    root, facts, docs, state = rule_source
    verified = p.load_official_rule(root, seal(root, facts, docs, state))
    assert verified.source_information_cutoff == NOW
    assert verified.event_date == date(2014, 10, 24)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("publisher", "PUBLISHER"),
        ("backdated", "CLOCK"),
        ("historical-time", "SCOPE"),
        ("new-rule", "SCOPE"),
        ("round-reference", "SCOPE"),
        ("extra-clause", "SCOPE"),
        ("quote", "QUOTE_CLOSURE"),
        ("offset", "QUOTE_CLOSURE"),
        ("requests", "BUDGET"),
        ("active", "BUDGET"),
    ],
)
def test_resealed_invalid_rule_claims_are_rejected(rule_source, mutation, error):
    root, facts, docs, state = rule_source
    if mutation == "publisher":
        docs[0]["url"] = "https://szse.cn.attacker.example/test"
    if mutation == "backdated":
        facts["source_information_cutoff"] = "2014-10-24T00:00:00+00:00"
    if mutation == "historical-time":
        facts["original_historical_acquisition_instant"] = "2014-10-24T00:00:00+00:00"
    if mutation == "new-rule":
        facts["rule_revision"] = "SZSE-TRADING-2026"
    if mutation == "round-reference":
        facts["reference_intermediate_rounding"] = "ROUND_FIRST"
    if mutation == "extra-clause":
        facts["clauses"]["invented"] = facts["clauses"]["rule_title"]
    if mutation == "quote":
        facts["clauses"]["rule_title"]["quote"] = "not present"
    if mutation == "offset":
        facts["clauses"]["rule_title"]["utf8_byte_start"] = 3
    if mutation == "requests":
        state["request_count"] = 11
    if mutation == "active":
        state["status"] = "ACTIVE"
    with pytest.raises(ValueError, match=error):
        p.load_official_rule(root, seal(root, facts, docs, state))


@pytest.mark.parametrize("mutation", ["byte", "extra", "symlink", "checksum", "digest"])
def test_rule_physical_mutation_fails_closed(rule_source, tmp_path, mutation):
    root, facts, docs, state = rule_source
    digest = seal(root, facts, docs, state)
    target = root / "rule-201311.body"
    if mutation == "byte":
        target.write_bytes(b"%PDF-changed")
    if mutation == "extra":
        (root / "unlisted").write_text("unexpected")
    if mutation == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
    if mutation == "checksum":
        (root / "SHA256SUMS").write_text("bad")
    if mutation == "digest":
        digest = "0" * 64
    with pytest.raises(ValueError):
        p.load_official_rule(root, digest)


@pytest.fixture
def inputs():
    facts = RightsIssueFacts(
        "synthetic",
        "a" * 64,
        "b" * 64,
        "000901",
        date(2014, 10, 15),
        date(2014, 10, 16),
        date(2014, 10, 22),
        date(2014, 10, 24),
        date(2014, 11, 4),
        NOW,
        Decimal("0.3"),
        Decimal("2.125"),
        290,
        1000,
    )
    rule = p.OfficialRuleFacts("c" * 64, NOW, date(2014, 10, 24))
    kwargs = {
        "previous_close": Decimal("10.015"),
        "limit_pct": Decimal("10"),
        "source_information_cutoff": NOW,
        "before_bar_sha256": "d" * 64,
        "before_limit_sha256": "e" * 64,
    }
    return facts, rule, kwargs


def test_actual_issue_ratio_exact_reference_and_context_independent_rounding(inputs):
    facts, rule, kwargs = inputs
    expected = (Fraction("10.015") * 1000 + Fraction("2.125") * 290) / 1290
    with localcontext() as ctx:
        ctx.prec = 2
        row = p.derive_reference(facts, rule, **kwargs)
    assert Fraction(int(row["reference_numerator"]), int(row["reference_denominator"])) == expected
    assert expected != (Fraction("10.015") + Fraction("2.125") * Fraction("0.3")) / Fraction("1.3")
    # Rounding the reference to 8.24 before multiplication would give a different upper bound.
    assert row["lower_limit_price"] == "7.42"
    assert row["upper_limit_price"] == "9.07"
    assert row["market_prices_modified"] is False
    assert row["historical_publication_instant"] is None
    assert row["qualified_scope"] == "EXACT_DERIVED_REFERENCE_NOT_WHOLE_REPLAY_ADMISSION"


@pytest.mark.parametrize(
    "price,expected", [("1.005", "1.01"), ("1.00499999", "1.00"), ("1.015", "1.02")]
)
def test_fractional_half_cent_rounds_half_up(price, expected):
    assert str(p._tick_round(Fraction(price))) == expected


@pytest.mark.parametrize(
    "mutation", ["code", "date", "clock", "naive", "nan", "float", "rate", "hash"]
)
def test_reference_rejects_scope_clock_precision_and_identity_changes(inputs, mutation):
    facts, rule, kwargs = inputs
    if mutation == "code":
        facts = replace(facts, security_code="000902")
    if mutation == "date":
        facts = replace(facts, ex_right_date=date(2014, 10, 25))
    if mutation == "clock":
        kwargs["source_information_cutoff"] = NOW.replace(year=2014)
    if mutation == "naive":
        kwargs["source_information_cutoff"] = NOW.replace(tzinfo=None)
    if mutation == "nan":
        kwargs["previous_close"] = Decimal("NaN")
    if mutation == "float":
        kwargs["previous_close"] = 10.015
    if mutation == "rate":
        kwargs["limit_pct"] = Decimal("20")
    if mutation == "hash":
        kwargs["before_limit_sha256"] = "bad"
    with pytest.raises(ValueError):
        p.derive_reference(facts, rule, **kwargs)
