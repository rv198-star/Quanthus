"""A qualified single repair never rewrites the original scientific authority."""

import copy
import json
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_historical_rights_successor as s

ROOT = Path(__file__).resolve().parents[2]


def test_current_successor_keeps_parent_scientific_scope_and_all_other_guards():
    original = (ROOT / s.h.REQUEST).read_bytes()
    request = s.load_request(ROOT)
    assert request["scope"] == ["2012-01-04", "2021-12-30", "2021-12-31"]
    assert list(request["arms"]) == ["B", "T", "TCH"]
    assert request["optional_rights_policy"] == "DO_NOT_SUBSCRIBE_UNIFORMLY"
    assert not request["source_guards_waived"]
    assert request["source_information_cutoff"].startswith("2026-10-24")
    assert request["original_execution_record"]["original_deadline_reset"] is False
    assert (ROOT / s.h.REQUEST).read_bytes() == original


@pytest.mark.parametrize(
    "mutation",
    ["window", "arm", "cash", "cutoff", "source", "policy", "missing-member", "bad-member"],
)
def test_changed_successor_cannot_admit_source_or_replay(tmp_path, monkeypatch, mutation):
    parent = s.h.load_request(ROOT)
    request = s.load_request(ROOT)
    for name in [s.h.REQUEST, *s.MEMBERS]:
        target = tmp_path / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes((ROOT / name).read_bytes())
    if mutation == "window":
        request["scope"][0] = "2015-01-01"
    if mutation == "arm":
        request["arms"]["B"]["trend"] = True
    if mutation == "cash":
        request["free_rights_or_dilution_compensation"] = True
    if mutation == "cutoff":
        request["source_information_cutoff"] = "2014-10-24T00:00:00+00:00"
    if mutation == "source":
        request["source_manifest_sha256"] = s.h.SOURCE_SHA
    if mutation == "policy":
        request["optional_rights_policy"] = "SUBSCRIBE"
    if mutation == "missing-member":
        del request["code_config_members"][next(iter(request["code_config_members"]))]
    if mutation == "bad-member":
        request["code_config_members"][next(iter(request["code_config_members"]))] = (
            "sha256:" + "0" * 64
        )
    (tmp_path / s.REQUEST).write_text(json.dumps(request))
    monkeypatch.setattr(s.h, "load_request", lambda _: copy.deepcopy(parent))
    with pytest.raises(ValueError, match="RIGHTS_SUCCESSOR"):
        s.load_request(tmp_path)


def test_prepare_binds_effective_source_and_new_cutoff_without_mutating_original(
    tmp_path, monkeypatch
):
    request = s.load_request(ROOT)
    parent = s.h.load_request(ROOT)
    parent_before = copy.deepcopy(parent)
    expected_bundle = object()
    seen = []

    def prepare(repository, bundle, output, actual):
        assert bundle is expected_bundle
        seen.append(actual["source_information_cutoff"])
        output.mkdir()
        return {"candidates": [], "request_digest": s.h.POPULATION_REQUEST_DIGEST}

    from types import SimpleNamespace

    bundle = SimpleNamespace(manifest_sha256=request["source_manifest_sha256"])
    expected_bundle = bundle
    monkeypatch.setattr(s.p10, "load_input", lambda root, sha: bundle)
    monkeypatch.setattr(s.h, "load_request", lambda _: copy.deepcopy(parent))
    monkeypatch.setattr(s.h, "prepare", prepare)
    result = s.prepare(ROOT, tmp_path / "source", tmp_path / "output", request)
    assert seen == ["2026-10-24T13:13:38.139930+00:00"]
    assert result["effective_source_manifest_sha256"] == request["source_manifest_sha256"]
    assert result["rights_successor_request_digest"] == s.digest_value(request)
    assert parent == parent_before


def test_bad_admission_fails_before_source_is_opened(tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "preparation.json").write_text("{}")
    (prepared / "features.parquet").write_bytes(b"synthetic")
    monkeypatch.setattr(
        s.p10, "load_input", lambda *args: pytest.fail("source read before admission")
    )
    with pytest.raises(ValueError, match="EXACT_ADMISSION"):
        s.HistoricalRightsRuntime(ROOT, tmp_path / "source", prepared, {})


def test_exact_admission_uses_new_source_clock_and_uniform_policy_with_original_engine(
    tmp_path, monkeypatch
):
    from contextlib import ExitStack
    from datetime import date, datetime
    from decimal import Decimal
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse.p10_rights_issue_source import RightsIssueFacts

    request = s.load_request(ROOT)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    preparation = {
        "request_digest": s.h.POPULATION_REQUEST_DIGEST,
        "rights_successor_request_digest": s.digest_value(request),
        "effective_source_manifest_sha256": request["source_manifest_sha256"],
        "source_information_cutoff": request["source_information_cutoff"],
        "optional_rights_policy_sha256": request["optional_rights_policy_sha256"],
    }
    (prepared / "preparation.json").write_text(json.dumps(preparation))
    (prepared / "features.parquet").write_bytes(b"synthetic-only")
    info = datetime.fromisoformat(request["source_information_cutoff"])
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
        info,
        Decimal("0.3"),
        Decimal("2.125"),
        290,
        1000,
    )
    monkeypatch.setattr(s, "load_rights_source", lambda *args: facts)
    marker = object()
    monkeypatch.setattr(s.p10, "load_input", lambda *args: marker)
    monkeypatch.setattr(
        s.h,
        "BoundedHistoricalReader",
        lambda bundle: SimpleNamespace(
            close=lambda: None,
            _rows=lambda _: [{"source_snapshot_id": s.h.ACTION_SOURCE_SNAPSHOT}],
        ),
    )

    def initialize(self, *args, **kwargs):
        self.stack = ExitStack()
        self.engine = SimpleNamespace(policy=None)

    monkeypatch.setattr(s.FormalP2ReplayRuntime, "__init__", initialize)
    admission = {
        "request_digest": s.digest_value(request),
        "role": "primary",
        "source_manifest_sha256": request["source_manifest_sha256"],
        "preparation_sha256": s.native._sha(prepared / "preparation.json"),
        "feature_sha256": s.native._sha(prepared / "features.parquet"),
    }
    with s.HistoricalRightsRuntime(ROOT, tmp_path / "source", prepared, admission) as runtime:
        assert runtime.bundle is marker
        assert runtime.info == info
        assert type(runtime.engine) is s.h.HistoricalEngine
        assert runtime.engine.action_snapshot == s.h.ACTION_SOURCE_SNAPSHOT
        assert [d["arm"] for d in runtime.rights_dispositions] == ["B", "T", "TCH"]
        assert all(
            d["subscription_share_delta"] == 0 and d["subscription_cash_delta"] == "0"
            for d in runtime.rights_dispositions
        )
        assert (
            runtime._parameter_payload("B")["source_manifest_sha256"]
            == request["source_manifest_sha256"]
        )
