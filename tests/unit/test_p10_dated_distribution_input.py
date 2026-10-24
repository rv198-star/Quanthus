"""Typed dated input keeps physical isolation and unavailable actual credit."""

from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import p10_dated_distribution_input as p
from tests.unit import test_p10_historical_distribution_source as fixture


def test_exact_official_fact_may_enter_new_type_without_relabelling_account_credit(tmp_path):
    root, event = fixture.source.__wrapped__(tmp_path)
    (fact,) = p._facts(root, fixture.seal(root, event))
    assert fact.share_credit_date is None
    assert fact.share_listing_date > fact.ex_date
    assert fact.acquired_at.year == 2026


@pytest.mark.parametrize("mode", ["same", "ancestor", "descendant", "link"])
def test_invalid_output_refused_before_any_parent_load_or_mkdir(tmp_path, monkeypatch, mode):
    source, official = tmp_path / "source", tmp_path / "official"
    source.mkdir()
    official.mkdir()
    (source / "unchanged").write_bytes(b"original")
    target = {"same": source, "ancestor": tmp_path, "descendant": source / "new"}.get(
        mode, tmp_path / "link"
    )
    if mode == "link":
        target.symlink_to(source, target_is_directory=True)
    before = {str(q): q.read_bytes() for q in source.iterdir()}
    monkeypatch.setattr(p.parent, "load_input", lambda *a: pytest.fail("parent opened"))
    with pytest.raises(ValueError, match="ISOLATION"):
        p.materialize(source, official, "a" * 64, target)
    assert {str(q): q.read_bytes() for q in source.iterdir()} == before
    if mode == "descendant":
        assert not target.exists()


def test_unrelated_manifest_cannot_fall_back_to_legacy_parent(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text("{}")
    (tmp_path / "SOURCE_READY").write_text("old\n")
    monkeypatch.setattr(p.parent, "load_input", lambda *a: pytest.fail("parent opened"))
    with pytest.raises(ValueError, match="IDENTITY"):
        p.load_input(tmp_path, "a" * 64)


def test_exact_input_code_has_separate_type_from_frozen_same_day_contract():
    assert p.VERSION != p.parent.VERSION
    assert p.PARENT_SHA == "777b6db164ba4070a8ad63c2074c9efe0b397ec134ecb994ba2055ea8cb371cb"
    assert Path(p.__file__).name != Path(p.parent.__file__).name
