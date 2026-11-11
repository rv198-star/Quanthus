"""Consumer behavior and fail-closed archive/package boundaries, using synthetic data."""

import gzip
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.expert_strategy import decision_review as review


@pytest.fixture
def archive(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "trace").mkdir(parents=True)
    (source / "facts").mkdir()
    cutoff = "2026-10-25T08:51:06.934678+00:00"
    held, trace, shadow = [], [], []
    support = []
    for i in range(3):
        code = "s1" if i < 2 else "s2"
        identity = dict(
            market_date="2020-01-02",
            security_code=code,
            unit_id=f"u{i}",
            entry_key=f"e{i}",
            entry_date="2020-01-01",
            quantity=100,
        )
        held.append(identity)
        baseline = "full_exit" if i == 1 else "no_action"
        fact = dict(
            security_code=code,
            market_date="2020-01-02",
            available=i < 2,
            reason="qualified" if i < 2 else "insufficient_history",
            lineage_digest="sha256:" + ("b" if i < 2 else "c") * 64,
            stage10_active=i < 2,
            stage11_active=False,
            signed_distance=None,
        )
        if i != 1:
            support.append(fact)
        feature = dict(
            feature_id="price.synthetic",
            feature_version="1.0.0",
            security_code=code,
            value="12.50000000000" if i < 2 else None,
            unavailable_reason=None if i < 2 else "unknown",
            lineage=dict(
                market_date="2020-01-02", snapshot_id="synthetic", knowledge_cutoff=cutoff
            ),
        )
        trace.append(
            {
                **identity,
                "baseline_action": baseline,
                "base_required_action": "force_liquidate" if i == 1 else "none",
                "base_reason_code": "base_constraint_stop_exit" if i == 1 else "clear",
                "baseline_decision_digest": "sha256:" + "a" * 64,
                "event_id": f"event-{i}",
                "intervention_id": "baseline",
                "context": dict(
                    security_code=code,
                    snapshot_id="synthetic",
                    position_state="trial",
                    knowledge_cutoff=cutoff,
                    feature_facts=[feature],
                ),
            }
        )
        shadow.append(
            {
                **identity,
                "baseline_action": baseline,
                "candidate_action": "reduce" if i == 0 else baseline,
                "candidate_digest": "sha256:" + "d" * 64,
                "baseline_decision_digest": "sha256:" + "a" * 64,
                "support_available": fact["available"],
                "support_reason": fact["reason"],
                "support_lineage_digest": fact["lineage_digest"],
                "proposed": i < 2,
                "final_action_changed": i == 0,
                "reason": "old_proposal" if i < 2 else "unknown_no_sell",
            }
        )
    inputs = dict(
        source_cutoff=cutoff,
        code_config_sha256={"original.py": "e" * 64},
        source_manifest_sha256="f" * 64,
    )

    def put(path, value, lines=False):
        raw = (
            b"".join(review.encoded(row) + b"\n" for row in value)
            if lines
            else review.encoded(value)
        )
        path.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == ".gz" else raw)

    put(source / "held-identities.TMP.private.json", {"rows": {"B": held}})
    put(source / "inputs.final.frozen.json", inputs)
    put(source / "trace/held-contexts.private.jsonl.gz", trace, lines=True)
    put(source / "decision-shadow.private.json.gz", shadow)
    put(source / "facts/selected.json", {"held_decision_inputs": support})
    expected = dict(
        reviews=3, episodes=3, issuers=2, unique_security_dates=2, unavailable_structure=1
    )
    pins = {
        str(p): {"bytes": p.stat().st_size, "sha256": review.sha(p.read_bytes())}
        for p in source.rglob("*")
        if p.is_file()
    }
    (tmp_path / "inventory").mkdir()
    selection = tmp_path / "inventory" / "selection.json"
    selection.write_bytes(
        review.encoded(dict(source_job=str(source), pins=pins, expected_population=expected))
    )
    contract = dict(
        schema_version="quanthus.historical-decision-review-contract.v1",
        purpose=review.PURPOSE,
        selection_sha256=review.sha(selection.read_bytes()),
        members=len(pins),
        input_bytes=sum(p["bytes"] for p in pins.values()),
        first_date="2012-01-01",
        last_date="2021-12-31",
        source_cutoff=cutoff,
        expected_population=expected,
        max_output_bytes=1024**2,
        minimum_free_bytes=0,
        historical_PIT_qualified=False,
        model_or_trading_admission="NOT_GRANTED",
    )
    monkeypatch.setattr(review, "load_contract", lambda root: contract)
    output = tmp_path / "products" / "review"
    output.parent.mkdir()
    return dict(
        source=source,
        selection=selection,
        contract=contract,
        output=output,
        root=tmp_path,
        original=(held, trace, shadow, support),
    )


def build(archive):
    return review.build_package(archive["selection"], archive["output"], archive["root"])


def test_real_contract_defaults_stay_retrospective_and_unadmitted():
    contract = review.load_contract(Path(__file__).resolve().parents[2])
    assert contract["expected_population"]["reviews"] == 8393
    assert contract["historical_PIT_qualified"] is False
    assert contract["model_or_trading_admission"] == "NOT_GRANTED"


def test_projection_keeps_multiple_events_unknowns_and_decimal_strings(archive):
    receipt = build(archive)
    manifest, rows = review.read_package(archive["output"], receipt["manifest_sha256"])
    result = review.query_rows(rows, security="s1", day="2020-01-02")
    assert result["matched"] == 2
    assert {r["identity"]["unit_id"] for r in result["rows"]} == {"u0", "u1"}
    assert rows[0]["features"][0]["value"] == "12.50000000000"
    assert rows[2]["features"][0]["value"] is None
    assert rows[2]["baseline"]["baseline_action"] == "no_action"
    assert rows[2]["structure"]["available"] is False
    assert rows[2]["snapshot"]["knowledge_cutoff"] == archive["contract"]["source_cutoff"]
    assert manifest["historical_PIT_qualified"] is False
    assert manifest["stages"]["projection_build_count"] == 1
    assert set(manifest["unobserved_alternatives"].values()) == {"NOT_OBSERVED_IN_HELD_TRACE"}
    assert review.query_rows(rows, security="absent")["returned"] == 0
    assert review.query_rows(rows, episode="e1")["rows"][0]["identity"]["unit_id"] == "u1"


@pytest.mark.parametrize("mode", ["alter", "delete", "symlink"])
def test_bad_input_fails_before_output_creation(archive, mode):
    p = archive["source"] / "facts/selected.json"
    if mode == "alter":
        p.write_bytes(p.read_bytes() + b" ")
    elif mode == "delete":
        p.unlink()
    else:
        other = p.with_name("other.json")
        p.rename(other)
        p.symlink_to(other)
    with pytest.raises((ValueError, OSError)):
        build(archive)
    assert not archive["output"].exists()


def test_wrong_selection_identity_and_output_overlap_do_not_write(archive):
    before = archive["selection"].read_bytes()
    archive["contract"]["selection_sha256"] = "0" * 64
    with pytest.raises(review.DecisionReviewError, match="SELECTION_CHANGED"):
        build(archive)
    archive["contract"]["selection_sha256"] = review.sha(before)
    archive["output"] = archive["source"] / "new-output"
    with pytest.raises(review.DecisionReviewError, match="SOURCE_OVERLAP"):
        build(archive)
    assert not archive["output"].exists()
    assert archive["selection"].read_bytes() == before


@pytest.mark.parametrize("defect", ["date", "duplicate", "feature", "unknown_sell"])
def test_semantic_invalidity_is_rejected_even_for_qualified_bytes(archive, defect):
    loaded = review._load_archive(archive["selection"], archive["contract"])
    if defect == "date":
        loaded["held"][0]["market_date"] = "2025-01-02"
    elif defect == "duplicate":
        loaded["trace"].append(deepcopy(loaded["trace"][0]))
    elif defect == "feature":
        loaded["trace"][0]["context"]["feature_facts"][0]["lineage"]["market_date"] = "2020-01-03"
    else:
        loaded["shadow"][2].update(
            proposed=True, final_action_changed=True, candidate_action="reduce"
        )
    with pytest.raises(ValueError):
        review.project_archive(loaded, archive["contract"])


def test_sealed_output_and_failed_write_are_never_overwritten(archive, monkeypatch):
    original_write = review._write

    def fail_manifest(path, value):
        if path.name == "manifest.json":
            raise OSError("synthetic interrupted write")
        original_write(path, value)

    monkeypatch.setattr(review, "_write", fail_manifest)
    with pytest.raises(OSError, match="interrupted"):
        build(archive)
    assert (archive["output"] / "reviews.jsonl").is_file()
    assert not (archive["output"] / "READY").exists()
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        build(archive)


def test_cold_query_never_opens_archive_and_corruption_cannot_fallback(archive, monkeypatch):
    receipt = build(archive)
    cold = archive["root"] / "cold"
    shutil.copytree(archive["output"], cold, copy_function=shutil.copyfile)
    real_read = Path.read_bytes

    def deny_sources(path):
        assert not path.is_relative_to(archive["source"]), "source dereferenced"
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", deny_sources)
    manifest, rows = review.read_package(cold, receipt["manifest_sha256"])
    assert len(rows) == 3
    assert manifest["consumer_admission"] == "RESEARCH_DIAGNOSTIC_ONLY"
    (cold / "reviews.jsonl").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="PAYLOAD_CHANGED"):
        review.read_package(cold, receipt["manifest_sha256"])
    (cold / "reviews.jsonl").unlink()
    with pytest.raises(ValueError, match="PHYSICAL_FILE_SET"):
        review.read_package(cold, receipt["manifest_sha256"])


def test_html_escapes_source_content_and_has_no_network_dependencies(archive):
    receipt = build(archive)
    manifest, rows = review.read_package(archive["output"], receipt["manifest_sha256"])
    rows[0]["baseline"]["base_reason_code"] = '<script>alert("source")</script>'
    view = review.render_html(
        review.query_rows(rows, limit=1), manifest, receipt["manifest_sha256"]
    )
    assert "&lt;script&gt;" in view
    assert "<script" not in view and 'src="http' not in view and 'href="http' not in view
    assert "default-src &#" not in view
    assert "default-src 'none'" in view and "不能视为历史当时已知" in view
    assert "2026-10-25T08:51:06.934678+00:00" in view


def test_query_is_bounded_and_reports_truncation(archive):
    receipt = build(archive)
    _, rows = review.read_package(archive["output"], receipt["manifest_sha256"])
    result = review.query_rows(rows, limit=1)
    assert result["matched"] == 3 and result["returned"] == 1 and result["truncated"] is True
    for limit in (0, 101, True):
        with pytest.raises(ValueError, match="QUERY_LIMIT"):
            review.query_rows(rows, limit=limit)


def test_query_cannot_add_files_to_immutable_package_or_original_archive(archive):
    receipt = build(archive)
    manifest, _ = review.read_package(archive["output"], receipt["manifest_sha256"])
    for root in (archive["output"], archive["source"]):
        output = root / "new-view.html"
        with pytest.raises(ValueError, match="QUERY_OUTPUT_OVERLAP"):
            review.write_query_output(output, b"view", archive["output"], manifest)
        assert not output.exists()
