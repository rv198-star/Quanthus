"""A parent clock must not turn two real source captures into inferred priority."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest, load_contract
from quanthus.pipelines.securities_warehouse.base22_f10_ownership import TABLES
from quanthus.pipelines.securities_warehouse.base22_ownership_floor_revision import (
    apply_floor_revision,
    qualify_floor_revisions,
    replay_floor_evidence,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
)
from tests.unit.test_base22_f10_ownership import CAPITAL, project, raw


def fixture():
    floor = datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc)
    resolver = FinancialCompanyResolver(
        [
            FinancialCompanyIdentity(
                "002001",
                "security",
                "company",
                datetime(2010, 1, 1, tzinfo=timezone.utc),
                None,
                floor,
            )
        ]
    )
    before = raw()
    before["acquired_at"] = "2026-07-31T22:26:51+08:00"
    new = raw(CAPITAL.replace("1.234567万", "2.345678万"))
    new["acquired_at"] = "2026-08-01T09:17:54+08:00"
    later = deepcopy(new)
    later["acquired_at"] = "2026-08-08T09:10:46+08:00"
    native = project(before, resolver)[0][TABLES[0]][0]
    sources = [
        {"raw": before, "source_sha256": "sha256:" + "a" * 64},
        {"raw": new, "source_sha256": "sha256:" + "b" * 64},
        {"raw": later, "source_sha256": "sha256:" + "c" * 64},
    ]
    from quanthus.pipelines.securities_warehouse.base22_f10_ownership import ownership_observation

    incoming = [
        ownership_observation(s["raw"], source_sha256=s["source_sha256"], resolver=resolver)[0][
            TABLES[0]
        ][0]
        for s in sources[1:]
    ]
    rule = load_contract(Path("config/pipelines"))[0]["tables"][TABLES[0]]
    observed = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    return native, sources, incoming, resolver, rule, observed


def qualify(native, sources, resolver, rule, observed):
    return qualify_floor_revisions(
        [native], sources, resolver=resolver, rule=rule, observed_at=observed
    )


def test_real_source_chronology_gets_a_new_governance_clock_and_keeps_old_atoms():
    native, sources, incoming, resolver, rule, observed = fixture()
    original = deepcopy([native, sources, incoming])
    proof = qualify(native, sources, resolver, rule, observed)
    key = digest([native[k] for k in rule["business_key"]])
    rows = apply_floor_revision([native, *incoming], rule, proof["revisions"][key])
    assert rows[0] == {**native, "knowledge_to": observed}
    assert rows[1]["share_quantity"] == incoming[0]["share_quantity"]
    assert rows[1]["raw_value"] == incoming[0]["raw_value"]
    assert rows[1]["knowledge_from"] == observed > incoming[-1]["knowledge_from"]
    assert rows[1]["source_revision"].startswith("p10-ownership-parent-floor-repair-v1:")
    assert rows[1]["source_artifact_sha256"] == proof["input_identity"]
    assert [native, sources, incoming] == original
    assert proof["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "unknown-clock",
        "reversed-clock",
        "other-provider",
        "wrong-native",
        "late-governance",
        "later-conflict",
    ],
)
def test_missing_chronology_or_actual_qualification_evidence_stays_closed(defect):
    native, sources, _, resolver, rule, observed = fixture()
    if defect == "unknown-clock":
        sources[0]["raw"]["acquired_at"] = None
    elif defect == "reversed-clock":
        sources[0]["raw"]["acquired_at"] = "2026-08-02T00:00:00Z"
    elif defect == "other-provider":
        sources[1]["raw"]["source"] = "another_provider"
    elif defect == "wrong-native":
        native["share_quantity"] += 1
    elif defect == "late-governance":
        observed -= timedelta(days=65)
    else:
        other = raw(CAPITAL.replace("1.234567万", "3.456789万"))
        other["acquired_at"] = sources[-1]["raw"]["acquired_at"]
        sources[-1]["raw"] = other
    with pytest.raises(RuntimeError):
        qualify(native, sources, resolver, rule, observed)


@pytest.mark.parametrize("defect", ["omitted", "new-atom", "old-atom", "repacked-proof"])
def test_the_transition_requires_the_exact_complete_original_group(defect):
    native, sources, incoming, resolver, rule, observed = fixture()
    proof = qualify(native, sources, resolver, rule, observed)
    key = digest([native[k] for k in rule["business_key"]])
    revision = proof["revisions"][key]
    rows = [native, *incoming]
    if defect == "omitted":
        rows.pop()
    elif defect == "new-atom":
        incoming[-1]["raw_value"] += "0"
    elif defect == "old-atom":
        native["source_revision"] = "other"
    else:
        revision["output"][1]["share_quantity"] += 1
    with pytest.raises(RuntimeError):
        apply_floor_revision(rows, rule, revision)


@pytest.mark.parametrize("defect", [None, "repacked-proof", "raw-body", "unconsumed-key"])
def test_actual_parquet_and_global_sort_reproduce_only_local_qualified_atoms(tmp_path, defect):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.base22 import canonical
    from quanthus.pipelines.securities_warehouse.base22_bounded import Budget, Limits
    from quanthus.pipelines.securities_warehouse.base22_ownership_versions import version_batches
    from quanthus.pipelines.securities_warehouse.ownership_capital import ownership_capital_schemas

    native, sources, incoming, resolver, rule, observed = fixture()
    schema = ownership_capital_schemas()[TABLES[0]]
    proof = qualify(native, sources, resolver, rule, observed)
    root = tmp_path / "evidence"
    root.mkdir()
    pq.write_table(pa.Table.from_pylist([native], schema=schema), root / "native.parquet")
    members = []
    for n, s in enumerate(sources):
        name = f"raw-{n}.parquet"
        pq.write_table(pa.Table.from_pylist([s["raw"]]), root / name)
        members.append(
            {"path": name, "source_sha256": s["source_sha256"], "raw_row_sha256": digest(s["raw"])}
        )
    item = {
        "native_before": "native.parquet",
        "raw_inputs": members,
        "observed_at": observed.isoformat(),
        "qualification": "proof.json",
    }
    if defect == "repacked-proof":
        proof["revisions"][next(iter(proof["revisions"]))]["output"][1]["raw_value"] += "0"
        proof["identity"] = digest({k: v for k, v in proof.items() if k != "identity"})
    elif defect == "raw-body":
        changed = deepcopy(sources[1]["raw"])
        changed["content"] += "extra non-modeled section"
        pq.write_table(pa.Table.from_pylist([changed]), root / "raw-1.parquet")
        members[1]["raw_row_sha256"] = digest(changed)
    (root / "proof.json").write_bytes(canonical(proof) + b"\n")
    if defect in {"repacked-proof", "raw-body"}:
        with pytest.raises(RuntimeError):
            replay_floor_evidence(root, item, resolver=resolver, rule=rule, schema=schema)
        return
    replay = replay_floor_evidence(root, item, resolver=resolver, rule=rule, schema=schema)
    assert replay == proof
    revisions = replay["revisions"]
    limits = Limits(allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2, free_floor_bytes=1)
    with Budget(tmp_path / "job", [root], limits) as budget:
        path = budget.root / "input.parquet"
        pq.write_table(pa.Table.from_pylist([native, *incoming], schema=schema), path)
        if defect == "unconsumed-key":
            revisions["sha256:" + "d" * 64] = deepcopy(next(iter(revisions.values())))
            with pytest.raises(RuntimeError, match="NOT_CONSUMED"):
                list(
                    version_batches(
                        TABLES[0], [path], rule, schema, budget, qualified_floor_revisions=revisions
                    )
                )
        else:
            result = [
                r
                for group in version_batches(
                    TABLES[0], [path], rule, schema, budget, qualified_floor_revisions=revisions
                )
                for r in group
            ]
            assert result == next(iter(revisions.values()))["output"]
