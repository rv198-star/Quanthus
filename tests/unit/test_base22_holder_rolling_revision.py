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


@pytest.mark.parametrize(
    "defect", [None, "raw-repacked", "input-artifact", "native-repacked", "typed-atom"]
)
def test_real_local_parquet_replay_and_bounded_global_sort_reject_repacked_changes(
    tmp_path, defect
):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import canonical
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base22_holder_rolling_revision import (
        replay_floor_evidence,
    )
    from quanthus.pipelines.securities_warehouse.base22_holder_versions import version_batches
    from quanthus.pipelines.securities_warehouse.ownership_capital import ownership_capital_schemas

    native, sources, incoming, resolver, rule, observed = fixture()
    schema = ownership_capital_schemas()["dwd_shareholding_observation"]
    proof = qualify_floor_revisions(
        native, sources, resolver=resolver, rule=rule, observed_at=observed
    )
    root = tmp_path / "job"
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(root, [], limits) as budget:
        pq.write_table(pa.Table.from_pylist(native, schema=schema), root / "native.parquet")
        members = []
        for n, s in enumerate(sources):
            name = f"raw-{n}.parquet"
            pq.write_table(pa.Table.from_pylist([s["raw"]]), root / name)
            members.append(
                {
                    "path": name,
                    "source_sha256": s["source_sha256"],
                    "raw_row_sha256": digest(s["raw"]),
                }
            )
        closure = {
            k: v
            for k, v in proof.items()
            if k
            not in {
                "input_identity",
                "revisions",
                "historical_PIT_admission",
                "consumer_admission",
                "identity",
            }
        }
        (root / "input.json").write_bytes(canonical(closure))
        (root / "proof.json").write_bytes(canonical(proof) + b"\n")
        item = {
            "native_before": "native.parquet",
            "raw_inputs": members,
            "observed_at": observed.isoformat(),
            "qualification": "proof.json",
            "input_artifact": "input.json",
        }
        paths = [root / "native.parquet"]
        for n, rows in enumerate(incoming):
            p = root / f"typed-{n}.parquet"
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
            paths.append(p)
        if defect == "raw-repacked":
            r = sources[-1]["raw"]
            changed = raw(r["content"].replace("1.234567万", "2.345678万", 1), "股东研究")
            changed["acquired_at"] = r["acquired_at"]
            pq.write_table(pa.Table.from_pylist([changed]), root / members[-1]["path"])
            members[-1]["raw_row_sha256"] = digest(changed)
        elif defect == "input-artifact":
            (root / "input.json").write_bytes(canonical(closure) + b" ")
        elif defect == "native-repacked":
            native[0]["holding_quantity"] += 1
            pq.write_table(pa.Table.from_pylist(native, schema=schema), paths[0])
        elif defect == "typed-atom":
            incoming[0][0]["holding_quantity"] += 1
            pq.write_table(pa.Table.from_pylist(incoming[0], schema=schema), paths[1])

        def evaluate():
            replay = replay_floor_evidence(root, item, resolver=resolver, rule=rule, schema=schema)
            return [
                r
                for group in version_batches(
                    "dwd_shareholding_observation",
                    paths,
                    rule,
                    schema,
                    budget,
                    qualified_floor_revisions=replay["revisions"],
                )
                for r in group
            ]

        if defect is not None:
            with pytest.raises(RuntimeError):
                evaluate()
        else:
            result = evaluate()
            assert len(result) == 180  # 80 retained, 80 new-clock repairs, 20 new-window atoms.
            assert sum(r["knowledge_to"] is not None for r in result) == 80


@pytest.mark.parametrize(
    "defect",
    [None, "source-member", "raw-binding", "typed-atom", "extra-index", "accepted-duplicate"],
)
def test_extra_sources_replay_original_members_and_only_originally_held_captures(tmp_path, defect):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base22_holder_versions import (
        replay_extra_holder_sources,
    )
    from quanthus.pipelines.securities_warehouse.ownership_capital import ownership_capital_schemas

    _, sources, _, resolver, _, _ = fixture()
    schema = ownership_capital_schemas()["dwd_shareholding_observation"]
    obs = {"raw_local_members": [], "source_members": []}
    body = {"floor_revision_evidence": {"raw_inputs": []}, "extra_holder_sources": []}
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "job", [], limits) as budget:
        root = budget.root
        observations = root / "observations"
        observations.mkdir()
        for n, s in enumerate(sources):
            r = s["raw"]
            if n in {3, 4} and defect != "accepted-duplicate":
                body_text = (
                    r["content"].removesuffix("└")
                    + "｜变动日期｜股东名称｜持股数｜变动股数｜变动后｜变动后占｜变动类型｜\n"
                    + "｜2026-08-01｜另一个合成法人｜1万｜-1万｜0｜0%｜变动｜\n└"
                )
                r = raw(body_text, "股东研究")
                r["acquired_at"] = s["raw"]["acquired_at"]
            name = f"raw-{n}.parquet"
            pq.write_table(pa.Table.from_pylist([r]), root / name)
            body["floor_revision_evidence"]["raw_inputs"].append(
                {"path": name, "raw_row_sha256": digest(r), "source_sha256": s["source_sha256"]}
            )
            if n:
                day = r["acquired_at"][:10]
                pq.write_table(pa.Table.from_pylist([r]), observations / name)
                obs["raw_local_members"].append({"path": name, "source_day": day})
                obs["source_members"].append({"day": day, "member": {"sha256": s["source_sha256"]}})
            if n in {3, 4}:
                typed, proof = holder_observation(
                    r, source_sha256=s["source_sha256"], resolver=resolver
                )
                name = f"extra-{n}.parquet"
                pq.write_table(
                    pa.Table.from_pylist(typed["dwd_shareholding_observation"], schema=schema),
                    root / name,
                )
                body["extra_holder_sources"].append(
                    {"source_index": n, "path": name, "projection_identity": proof["identity"]}
                )
        if defect == "source-member":
            obs["source_members"][0]["member"]["sha256"] = "sha256:" + "f" * 64
        elif defect == "raw-binding":
            pq.write_table(
                pa.Table.from_pylist([sources[2]["raw"]]), observations / "raw-1.parquet"
            )
        elif defect == "typed-atom":
            p = root / "extra-3.parquet"
            rows = pq.ParquetFile(p).read().to_pylist()
            rows[0]["holding_quantity"] += 1
            pq.write_table(pa.Table.from_pylist(rows, schema=schema), p)
        elif defect == "extra-index":
            body["extra_holder_sources"][0]["source_index"] = 2

        def evaluate():
            return replay_extra_holder_sources(
                root, body, observations, obs, resolver, schema, budget
            )

        if defect is not None:
            with pytest.raises(RuntimeError):
                evaluate()
        else:
            paths, report = evaluate()
            assert len(paths) == 2
            assert report["separate_change_domain"] == "NOT_GRANTED"
            assert [i["predecessor"] for i in report["linked_original_captures"]] == [
                "QUALIFIED_TYPED_ATOMS_UNCHANGED"
            ] * 2 + ["HELD_NONEMPTY_RAW_CATEGORY"] * 2
