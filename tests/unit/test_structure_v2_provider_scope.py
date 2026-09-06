from __future__ import annotations

import json
from datetime import date

from quanthus.backtest.data_provider import load_replay_data_provider_config
from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import calculation_spec, digest
from quanthus.structure_v2_run import REQUEST_PATH, load_request

ROOT = find_project_root()
ORIGINAL = "config/replay-data-provider.development.v2.json"
BOUNDED = "config/replay-data-provider.structure-v2.development.v1.json"


def test_bounded_provider_preserves_authority_and_only_trims_unused_materialization_dates():
    old = json.loads((ROOT / ORIGINAL).read_text())
    new = json.loads((ROOT / BOUNDED).read_text())
    assert set(old) == set(new)
    for key in set(old) - {"provider_id", "scope", "materialization"}:
        assert old[key] == new[key], key
    for key in set(old["materialization"]) - {"history_start"}:
        assert old["materialization"][key] == new["materialization"][key], key
    assert old["materialization"]["history_start"] == "2011-01-01"
    assert old["scope"]["execution_end"] == "2021-12-31"
    assert new["scope"]["partition"] == old["scope"]["partition"] == "development"
    assert new["scope"]["reader_role"] == old["scope"]["reader_role"]
    assert new["materialization"]["per_decision_publication_proven"] is False

    request = load_request(ROOT / REQUEST_PATH)
    config = load_replay_data_provider_config(ROOT / BOUNDED)
    assert request["provider_config"] == BOUNDED
    assert config.history_start == date(2019, 9, 30)
    assert config.history_start < date.fromisoformat(request["scan_start"])
    assert config.scope.decision_start == date.fromisoformat(request["scan_start"])
    assert config.scope.decision_end == date.fromisoformat(request["report_end"])
    assert config.scope.execution_end == date(2020, 4, 1)
    config.scope.authorize(config.scope.decision_start, config.scope.decision_end)


def test_provider_scope_repair_does_not_change_frozen_question_or_calculation_spec():
    request = load_request(ROOT / REQUEST_PATH)
    before = {**request, "provider_config": ORIGINAL}
    assert digest(calculation_spec(request)) == digest(calculation_spec(before))
    assert digest(calculation_spec(request)) == (
        "sha256:e405f68fd1efed10c3e02cc643a35c2b479cace074c9df7bfca75ef0e0c46cc1"
    )
    assert digest(request) != digest(before)
    assert request["security_codes"] == ["000001", "300750", "600519"]
    assert request["lookbacks"] == [20, 60]
    assert request["scan_start"] == "2019-10-08"
    assert request["report_start"] == "2020-01-02"
    assert request["report_end"] == "2020-03-31"
    assert request["cold_result_job_budget"] == 2
