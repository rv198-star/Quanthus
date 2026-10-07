"""A rolling source window is not deletion of an older historical frame."""

from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract
from quanthus.pipelines.securities_warehouse.base22_holder_literals import holder_observation
from quanthus.pipelines.securities_warehouse.base22_holder_rolling_revision import (
    apply_floor_revision,
    qualify_floor_revisions,
)
from tests.unit.test_base22_f10_ownership import raw
from tests.unit.test_base22_ownership_floor_revision import fixture as capital_fixture

OLD_DAYS = ["2026-03-30", "2025-12-30", "2025-09-29", "2025-06-29"]
NEW_DAYS = ["2026-06-29", *OLD_DAYS[:3]]


def content(days, token):
    return "\n".join(
        f"截至日期：{day} 十大{floating}股东情况\n"
        + "｜股东名称｜持股数量｜占总股本｜股份性质｜\n"
        + "\n".join(f"｜合成股东{rank}｜1.234567万｜{token}｜普通股｜" for rank in range(1, 11))
        + "\n└"
        for day in days
        for floating in ["", "流通"]
    )


def fixture():
    _, _, _, resolver, _, observed = capital_fixture()
    clocks = [
        "2026-07-31T21:35:18+08:00",
        "2026-08-01T08:02:38+08:00",
        "2026-08-08T08:02:34+08:00",
        "2026-08-22T08:02:12+08:00",
        "2026-08-29T08:02:44+08:00",
    ]
    sources, parsed = [], []
    for i, clock in enumerate(clocks):
        source = raw(
            content(
                OLD_DAYS if i < 3 else NEW_DAYS,
                "1.123456789012 占A股" if i == 0 else "1.123456789012%",
            ),
            "股东研究",
        )
        source["acquired_at"] = clock
        item = {"raw": source, "source_sha256": "sha256:" + "abcde"[i] * 64}
        sources.append(item)
        parsed.append(
            holder_observation(source, source_sha256=item["source_sha256"], resolver=resolver)[0][
                "dwd_shareholding_observation"
            ]
        )
    rule = load_contract(Path("config/pipelines"))[0]["tables"]["dwd_shareholding_observation"]
    return parsed[0], sources, parsed[1:], resolver, rule, observed


def test_complete_four_frame_roll_preserves_80_native_keys_and_real_capture_clocks():
    native, sources, incoming, resolver, rule, observed = fixture()
    original = deepcopy([native, sources, incoming])
    proof = qualify_floor_revisions(
        native, sources, resolver=resolver, rule=rule, observed_at=observed
    )
    assert len(proof["revisions"]) == 80
    assert proof["out_of_window_absences"] == 40  # 20 keys absent in each of two captures.
    assert proof["new_window_observations"] == 40
    for old in native:
        key = digest([old[k] for k in rule["business_key"]])
        group = [
            old,
            *[
                r
                for rows in incoming
                for r in rows
                if digest([r[k] for k in rule["business_key"]]) == key
            ],
        ]
        output = apply_floor_revision(group, rule, proof["revisions"][key])
        assert output[0] == {**old, "knowledge_to": observed}
        assert output[1]["knowledge_from"] == observed
        assert output[1]["holding_percentage"] == old["holding_percentage"]
        assert output[1]["raw_holding_percentage"] == "1.123456789012%"
    assert [native, sources, incoming] == original
    assert proof["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "missing-rank",
        "missing-frame",
        "wrong-name",
        "old-hole",
        "backwards-roll",
        "later-body",
        "wrong-native",
        "other-provider",
        "unknown-clock",
        "missing-native",
    ],
)
def test_only_proven_complete_forward_windows_allow_out_of_window_absences(defect):
    native, sources, _, resolver, rule, observed = fixture()
    item = sources[-1]
    body = item["raw"]["content"]
    if defect == "missing-rank":
        body = body.replace("｜合成股东10｜1.234567万｜1.123456789012%｜普通股｜", "", 1)
    elif defect == "missing-frame":
        body = content(NEW_DAYS[:3], "1.123456789012%")
    elif defect == "wrong-name":
        body = body.replace("合成股东1｜", "其他合成股东1｜", 1)
    elif defect == "old-hole":
        body = content([NEW_DAYS[0], NEW_DAYS[1], NEW_DAYS[3], OLD_DAYS[-1]], "1.123456789012%")
    elif defect == "backwards-roll":
        body = content(OLD_DAYS, "1.123456789012%")
    elif defect == "later-body":
        body = body.replace("1.234567万", "2.345678万", 1)
    elif defect == "wrong-native":
        native[0]["holding_quantity"] += 1
    elif defect == "missing-native":
        native.pop()
    elif defect == "other-provider":
        item["raw"]["source"] = "another_provider"
    elif defect == "unknown-clock":
        item["raw"]["acquired_at"] = None
    if defect in {
        "missing-rank",
        "missing-frame",
        "wrong-name",
        "old-hole",
        "backwards-roll",
        "later-body",
    }:
        changed = raw(body, "股东研究")
        changed["acquired_at"] = item["raw"]["acquired_at"]
        item["raw"] = changed
    with pytest.raises(RuntimeError):
        qualify_floor_revisions(native, sources, resolver=resolver, rule=rule, observed_at=observed)


def test_repacked_proof_and_omitted_capture_cannot_bypass_exact_whole_group():
    native, sources, incoming, resolver, rule, observed = fixture()
    proof = qualify_floor_revisions(
        native, sources, resolver=resolver, rule=rule, observed_at=observed
    )
    old = native[0]
    key = digest([old[k] for k in rule["business_key"]])
    group = [
        old,
        *[
            r
            for rows in incoming
            for r in rows
            if digest([r[k] for k in rule["business_key"]]) == key
        ],
    ]
    with pytest.raises(RuntimeError):
        apply_floor_revision(group[:-1], rule, proof["revisions"][key])
    changed = deepcopy(proof["revisions"][key])
    changed["output"][1]["holding_quantity"] += 1
    with pytest.raises(RuntimeError):
        apply_floor_revision(group, rule, changed)
