"""Exact held-key source admission and pre-write physical isolation."""

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse import p10_tch_held_action_input as p
from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts


def fact():
    day = date(2015, 5, 15)
    return ActionFacts(
        "000748",
        date(2015, 5, 14),
        day,
        day,
        day,
        day,
        Decimal("0.07"),
        Decimal("1"),
        datetime(2026, 10, 25, tzinfo=timezone.utc),
        "synthetic-only",
        "a" * 64,
        "b" * 64,
    )


def test_exact_same_day_facts_retain_observation_not_historical_knowledge(tmp_path, monkeypatch):
    f = fact()
    monkeypatch.setattr(p.official, "load_source", lambda *a: (f,))
    (result,) = p._facts(tmp_path, p.OFFICIAL_SHA)
    assert result == f and result.acquired_at.year == 2026
    assert result.ex_date.year == 2015


@pytest.mark.parametrize(
    "field,value",
    [
        ("security_code", "000749"),
        ("ex_date", date(2015, 5, 18)),
        ("cash_payment_date", None),
        ("share_credit_date", None),
        ("share_listing_date", date(2015, 5, 18)),
    ],
)
def test_fully_qualified_other_terms_cannot_enter_narrow_scope(tmp_path, monkeypatch, field, value):
    f = replace(fact(), **{field: value})
    monkeypatch.setattr(p.official, "load_source", lambda *a: (f,))
    with pytest.raises(ValueError, match="SOURCE_DATES_OR_KEY"):
        p._facts(tmp_path, p.OFFICIAL_SHA)


def test_different_source_identity_refused_before_source_read(tmp_path, monkeypatch):
    monkeypatch.setattr(p.official, "load_source", lambda *a: pytest.fail("source opened"))
    with pytest.raises(ValueError, match="SOURCE_IDENTITY"):
        p._facts(tmp_path, "a" * 64)


@pytest.mark.parametrize("mode", ["same", "ancestor", "descendant", "link"])
def test_isolation_precedes_parent_load_and_all_writes(tmp_path, monkeypatch, mode):
    source, official = tmp_path / "source", tmp_path / "official"
    source.mkdir()
    official.mkdir()
    (source / "unchanged").write_bytes(b"retained before-image")
    target = {"same": source, "ancestor": tmp_path, "descendant": source / "new"}.get(
        mode, tmp_path / "link"
    )
    if mode == "link":
        target.symlink_to(source, target_is_directory=True)
    before = {q.name: q.read_bytes() for q in source.iterdir()}
    monkeypatch.setattr(p.parent, "load_input", lambda *a: pytest.fail("parent opened"))
    with pytest.raises(ValueError, match="ISOLATION"):
        p.materialize(source, official, p.OFFICIAL_SHA, target)
    assert {q.name: q.read_bytes() for q in source.iterdir()} == before
    if mode == "descendant":
        assert not target.exists()


def test_unknown_envelope_does_not_fall_back_to_old_source(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text("{}")
    (tmp_path / "SOURCE_READY").write_text("old\n")
    monkeypatch.setattr(p.parent, "load_input", lambda *a: pytest.fail("old source opened"))
    with pytest.raises(ValueError, match="IDENTITY"):
        p.load_input(tmp_path, "a" * 64)
