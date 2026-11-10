"""All retained raw captures must support a truthful holder governance repair."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract
from quanthus.pipelines.securities_warehouse.base22_holder_floor_revision import (
    apply_floor_revision,
    qualify_floor_revisions,
)
from quanthus.pipelines.securities_warehouse.base22_holder_literals import holder_observation
from tests.unit.test_base22_f10_ownership import HOLDERS, raw
from tests.unit.test_base22_ownership_floor_revision import fixture as capital_fixture


def fixture():
    _, _, _, resolver, _, observed = capital_fixture()
    before = raw(HOLDERS.replace("1.123456789012%", "1.123456789012 占A股"), "股东研究")
    before["acquired_at"] = "2026-07-31T21:35:18+08:00"
    after = raw(HOLDERS, "股东研究")
    after["acquired_at"] = "2026-08-01T08:02:38+08:00"
    later = deepcopy(after)
    later["acquired_at"] = "2026-08-08T08:02:34+08:00"
    sources = [
        {"raw": r, "source_sha256": "sha256:" + x * 64}
        for r, x in zip([before, after, later], "abc", strict=True)
    ]
    rows = [
        holder_observation(s["raw"], source_sha256=s["source_sha256"], resolver=resolver)[0][
            "dwd_shareholding_observation"
        ][0]
        for s in sources
    ]
    rule = load_contract(Path("config/pipelines"))[0]["tables"]["dwd_shareholding_observation"]
    return rows[0], sources, rows[1:], resolver, rule, observed


def test_reproduced_native_and_all_later_source_atoms_get_a_new_governance_clock():
    native, sources, incoming, resolver, rule, observed = fixture()
    old = deepcopy([native, sources, incoming])
    proof = qualify_floor_revisions(
        [native], sources, resolver=resolver, rule=rule, observed_at=observed
    )
    key = digest([native[k] for k in rule["business_key"]])
    output = apply_floor_revision([native, *incoming], rule, proof["revisions"][key])
    assert output[0] == {**native, "knowledge_to": observed}
    assert output[0]["raw_holding_percentage"].endswith("占A股")
    assert output[1]["holding_percentage"] == incoming[0]["holding_percentage"]
    assert output[1]["knowledge_from"] == observed
    assert proof["historical_PIT_admission"] is False
    assert [native, sources, incoming] == old


@pytest.mark.parametrize(
    "defect",
    [
        "wrong-native",
        "other-provider",
        "unknown-clock",
        "later-conflict",
        "raw-denominator",
        "duplicate-native",
        "missing-key",
    ],
)
def test_missing_or_conflicting_real_source_closure_cannot_be_qualified(defect):
    native, sources, _, resolver, rule, observed = fixture()
    natives = [native]
    if defect == "wrong-native":
        native["holding_quantity"] += 1
    elif defect == "other-provider":
        sources[1]["raw"]["source"] = "another_provider"
    elif defect == "unknown-clock":
        sources[0]["raw"]["acquired_at"] = None
    elif defect == "duplicate-native":
        natives.append(deepcopy(native))
    else:
        body = HOLDERS
        if defect == "later-conflict":
            body = body.replace("1.234567万", "2.345678万")
        elif defect == "raw-denominator":
            body = body.replace("1.123456789012%", "1.123456789012 占H股")
        else:
            body = body.replace("合成股东", "其他合成股东")
        changed = raw(body, "股东研究")
        changed["acquired_at"] = sources[-1]["raw"]["acquired_at"]
        sources[-1]["raw"] = changed
    with pytest.raises(RuntimeError):
        qualify_floor_revisions(
            natives, sources, resolver=resolver, rule=rule, observed_at=observed
        )


def test_the_exact_whole_group_is_required_and_source_times_never_serve_as_governance_time():
    native, sources, incoming, resolver, rule, observed = fixture()
    proof = qualify_floor_revisions(
        [native], sources, resolver=resolver, rule=rule, observed_at=observed
    )
    key = digest([native[k] for k in rule["business_key"]])
    with pytest.raises(RuntimeError):
        apply_floor_revision([native, incoming[0]], rule, proof["revisions"][key])
    with pytest.raises(RuntimeError):
        qualify_floor_revisions(
            [native],
            sources,
            resolver=resolver,
            rule=rule,
            observed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )
