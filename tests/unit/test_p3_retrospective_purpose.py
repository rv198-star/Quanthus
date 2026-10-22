"""Late warehouse clocks may support a weaker purpose, never strict PIT claims."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_base24_native as native
from quanthus.pipelines.securities_warehouse import p3_purpose as purpose
from tests.unit.test_p3_base24_native import (
    CLOCK,
    TZ,
    mutate,
    sha,
)
from tests.unit.test_p3_base24_native import (
    source as source_fixture,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def source(tmp_path):
    return source_fixture.__wrapped__(tmp_path)


def run(source, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return purpose.assess(
        source,
        manifest_sha256=sha(source / "manifest.json"),
        repository=ROOT,
        warmup=json.loads((source.parent / "synthetic-warmup-authority.json").read_text()),
        temp_parent=scratch,
    )


def test_late_snapshot_input_does_not_become_historical_visibility(source, tmp_path):
    late = datetime(2026, 10, 1, tzinfo=TZ)
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=late))
    before = {p.name: sha(p) for p in (source / "native").iterdir()}
    result = run(source, tmp_path)
    assert result["status"] == "RETROSPECTIVE_SOURCE_CENSUS_PASS_WITH_LIMITATIONS"
    assert (
        result["tables"]["dwd_security_bar"]["classes"]["warehouse_known_after_observation_end"]
        == 1
    )
    assert result["original_B_accepted"] is result["D_activated"] is False
    assert result["historical_visibility_proven"] is False
    with native.NativeView(source) as view:
        view.at(CLOCK)
        assert view.connection.execute("SELECT count(*) FROM dwd_security_bar").fetchone()[0] == 0
    assert before == {p.name: sha(p) for p in (source / "native").iterdir()}
    assert "1.1000000001" not in json.dumps(result)


def test_unknown_clocks_are_counted_and_excluded_not_assumed(source, tmp_path):
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=None))
    result = run(source, tmp_path)
    table = result["tables"]["dwd_security_bar"]
    assert table["unknown_knowledge"] == 1
    assert table["snapshot_rows"] == 0
    assert result["original_4173_policy_preserved_not_remeasured"] is True


def test_snapshot_is_frozen_not_latest_current(source, tmp_path):
    mutate(
        source,
        "dwd_security_bar",
        lambda rows: rows[0].update(knowledge_from=datetime(2026, 11, 1, tzinfo=TZ)),
    )
    result = run(source, tmp_path)
    assert result["tables"]["dwd_security_bar"]["snapshot_rows"] == 0


def test_conflicting_versions_are_not_admitted_by_retrospective_label(source, tmp_path):
    mutate(
        source,
        "dwd_security_bar",
        lambda rows: rows.append({**rows[0], "source_revision": "conflict"}),
    )
    with pytest.raises(native.InputError, match="PURPOSE_AMBIGUOUS_SNAPSHOT_VERSION"):
        run(source, tmp_path)


def test_wrong_existing_calendar_session_still_blocks(source, tmp_path):
    mutate(
        source,
        "dwd_tradability_state",
        lambda rows: rows[0].update(calendar_session_id="sz-2025-01-03"),
    )
    result = run(source, tmp_path)
    assert result["status"] == "BLOCKED_CRITICAL_INPUT"
    assert "PURPOSE_DATE_VENUE_IDENTITY:dwd_tradability_state" in result["critical_failures"]


def test_bad_lineage_is_not_a_low_priority_time_limitation(source, tmp_path):
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(source_artifact_sha256=None))
    result = run(source, tmp_path)
    assert "PURPOSE_KEY_OR_LINEAGE:dwd_security_bar" in result["critical_failures"]


def test_reading_corrupt_source_fails_before_qualification(source, tmp_path):
    path = source / "native/dwd_security_bar.parquet"
    path.write_bytes(path.read_bytes() + b"damaged")
    with pytest.raises(native.InputError, match="MEMBER_CHANGED_OR_INVALID"):
        run(source, tmp_path)


def test_purpose_contract_cannot_be_used_as_execution_authority(tmp_path):
    path = tmp_path / purpose.POLICY
    path.parent.mkdir(parents=True)
    policy = json.loads((ROOT / purpose.POLICY).read_text())
    policy["allowed"].append("D result execution")
    path.write_text(json.dumps(policy))
    with pytest.raises(native.InputError, match="PURPOSE_POLICY_DIFFERS"):
        purpose.load_policy(tmp_path)
