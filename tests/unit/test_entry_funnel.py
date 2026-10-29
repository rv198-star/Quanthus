"""The delivered historical report preserves unknowns and uses actual order/fill ids."""

import json
import shutil
from copy import deepcopy

import pytest

from quanthus.expert_strategy import decision_chain as chain
from quanthus.expert_strategy import entry_funnel as funnel
from tests.unit import test_decision_chain as fixtures

bundle = fixtures.bundle
build = fixtures.build


def report_for(bundle):
    build(bundle)
    digest = chain.sha((bundle["output"] / "manifest.json").read_bytes())
    manifest, data, index = chain.read_package(bundle["output"], digest)
    return funnel.project_funnel(data, index, manifest, digest), manifest, data, index


def test_funnel_reconciles_orders_and_retains_unknown_allocation(bundle):
    report, manifest, data, index = report_for(bundle)
    assert report["counts"] == dict(
        opportunities=4,
        trial_entry_actions=4,
        other_actions=0,
        scoring_abstentions=0,
        trial_entries_without_order=1,
        entries_with_order=3,
        buy_orders=3,
        filled_entries=2,
        entries_ordered_not_filled=1,
    )
    assert report["allocation"]["recorded_results"] == 0
    assert report["allocation"]["trial_entry_results_not_recorded"] == 4
    assert report["allocation"]["no_order_trial_entry_reasons_not_recorded"] == 1
    assert report["allocation"]["status"] == "NOT_RECORDED_IN_SELECTED_MANIFEST_MEMBERS"
    assert report["unfilled_buy_status_reasons"] == {"buy_blocked_limit_up": 1}
    assert report["purpose"] == funnel.PURPOSE
    assert report["contract_sha256"] == manifest["contract_sha256"]
    assert report["source_members"] == manifest["members"]
    for example in report["examples"].values():
        assert example["allocation_reason"] == "NOT_RECORDED"
        assert data["decisions"][example["decision_row"]]["decision_key"] == example["entry_key"]
        assert (
            example["formal_decision_digest"]
            == data["decisions"][example["decision_row"]]["formal_decision_digest"]
        )
        assert all(
            data["events"][i]["event_type"] == "order_created" for i in example["order_event_rows"]
        )
    assert report["execution_authorized"] is report["historical_PIT_qualified"] is False


def test_legacy_filled_and_allocated_flags_are_not_reservation_or_fill_evidence(bundle):
    report, manifest, data, index = report_for(bundle)
    altered = deepcopy(data)
    for d in altered["decisions"]:
        d["filled"] = not d["filled"]
        d["allocated"] = not d["allocated"]
    changed = funnel.project_funnel(
        altered, index, manifest, report["trusted_chain_manifest_sha256"]
    )
    assert changed["counts"] == report["counts"]
    assert changed["allocation"] == report["allocation"]
    assert changed["groups"] != report["groups"]  # original flag still displayed honestly


def test_scoring_abstention_is_only_counted_from_recorded_reason(bundle):
    report, manifest, data, index = report_for(bundle)
    copy = deepcopy(data)
    copy["decisions"][1].update(
        final_action="no_action", allocated=False, reason_codes=["all_scoring_abstained_block_new"]
    )
    report = funnel.project_funnel(copy, index, manifest, report["trusted_chain_manifest_sha256"])
    assert report["counts"]["scoring_abstentions"] == 1
    assert report["counts"]["trial_entry_actions"] == 3
    assert report["counts"]["trial_entries_without_order"] == 0
    assert report["counts"]["other_actions"] == 1


def test_html_is_offline_escaped_and_explains_allocation_gap(bundle):
    report, _, _, _ = report_for(bundle)
    report["scope"]["source_cutoff"] = '<img src="https://invalid" onerror="bad()">'
    rendered = funnel.render_funnel(report)
    assert "原因未记录" in rendered and "资金已预留" in rendered
    assert "<img" not in rendered and "&lt;img" in rendered
    assert "default-src 'none'" in rendered and "form-action 'none'" in rendered
    assert "script" not in rendered and "fetch(" not in rendered


def test_cli_reads_package_without_original_sources_and_writes_exclusive_outputs(
    bundle,
    monkeypatch,
    tmp_path,
):
    report, _, _, _ = report_for(bundle)
    shutil.rmtree(bundle["source"])
    json_out, html_out = tmp_path / "funnel.json", tmp_path / "funnel.html"
    monkeypatch.setattr(
        "sys.argv",
        [
            "funnel",
            "--package",
            str(bundle["output"]),
            "--manifest-sha256",
            report["trusted_chain_manifest_sha256"],
            "--json",
            str(json_out),
            "--html",
            str(html_out),
        ],
    )
    funnel.main()
    assert json.loads(json_out.read_bytes()) == report
    assert "从入场动作到成交" in html_out.read_text()
    before = json_out.read_bytes()
    with pytest.raises((FileExistsError, ValueError)):
        funnel.main()
    assert json_out.read_bytes() == before


def test_bad_trusted_digest_rejects_before_any_output(bundle, monkeypatch, tmp_path):
    build(bundle)
    out = tmp_path / "must-not-exist.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "funnel",
            "--package",
            str(bundle["output"]),
            "--manifest-sha256",
            "0" * 64,
            "--json",
            str(out),
            "--html",
            str(out.with_suffix(".html")),
        ],
    )
    with pytest.raises(chain.DecisionChainError, match="CHAIN_MANIFEST_CHANGED"):
        funnel.main()
    assert not out.exists() and not out.with_suffix(".html").exists()


def test_funnel_source_output_overlap_rejected(bundle, monkeypatch, tmp_path):
    report, _, _, _ = report_for(bundle)
    out = bundle["output"] / "must-not-exist.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "funnel",
            "--package",
            str(bundle["output"]),
            "--manifest-sha256",
            report["trusted_chain_manifest_sha256"],
            "--json",
            str(out),
            "--html",
            str(tmp_path / "new.html"),
        ],
    )
    with pytest.raises(chain.DecisionChainError, match="CHAIN_QUERY_SOURCE_OVERLAP"):
        funnel.main()
    assert not out.exists()
