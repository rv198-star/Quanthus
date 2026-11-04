from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Budget, Limits
from quanthus.pipelines.securities_warehouse.base_build_inputs import identity
from quanthus.pipelines.securities_warehouse.base_build_quality import (
    CONTRACT,
    EXPECTED,
    POLICY,
    evaluate_historical,
    validate_policy,
)
from tests.test_base_build_ods import save


def fixture(tmp_path, *, matching=True, registered=False, inherited=False):
    """Selector-only synthetic fixture, never a native or paired acceptance."""
    ods, dwd = tmp_path / "ods", tmp_path / "dwd"
    save(
        ods / "evidence/frozen-inputs.json",
        {
            "content": {
                "selection": {"evidence": [{"purpose": "historical_quality"}] if registered else []}
            }
        },
    )
    save(
        ods / "MATERIALIZATION.json",
        {"sources": {"previous": {"role": "ods", "prefix": "members/previous"}}},
    )
    manifest = {"kind": "quanthus_ods_base24_bundle"}
    if inherited:
        gate = {
            "contract": CONTRACT,
            "cases": [
                {
                    "id": cid,
                    "venue": venue,
                    "stock_code": code,
                    "market_date": day,
                    "observations_present": False,
                    "scope_registered": True,
                    "status": "PENDING_GOVERNED_DISPOSITION",
                    "facts_or_grades_changed": False,
                    "held_added": 0,
                }
                for cid, venue, code, day in sorted(EXPECTED)
            ],
        }
        gate["identity"] = identity(gate)
        manifest["pairing"] = {"historical_gate": gate}
    save(ods / "members/previous/manifest.json", manifest)
    tables = {
        "dwd_listing": (
            "all",
            pa.table(
                {"listing_id": ["listing"], "venue_id": ["venue"], "listing_code": ["300114"]}
            ),
        ),
        "dwd_trading_venue": ("all", pa.table({"venue_id": ["venue"], "mic_code": ["XSHE"]})),
        "dwd_security_bar": (
            "interval_size=1d/observation_start_year=2025/observation_start_month=2",
            pa.table(
                {
                    "listing_id": ["listing"],
                    "observation_start": [
                        datetime(2025, 2, 17 if matching else 18, 2, tzinfo=timezone.utc)
                    ],
                }
            ),
        ),
    }
    layout = {}
    for table, (part, data) in tables.items():
        path = dwd / "payload" / table / "part.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(data, path)
        layout[table] = {"files": [{"path": path.relative_to(dwd).as_posix(), "partition": part}]}
    save(dwd / "MATERIALIZATION.json", {"layout": layout})
    return ods, dwd


def execute(tmp_path, f):
    with Budget(tmp_path / "quality", list(f), Limits(free_floor_bytes=1)) as budget:
        return evaluate_historical(*f, budget)


def test_registered_observation_blocks_publication_without_changing_values_or_held(tmp_path):
    f = fixture(tmp_path)
    before = {p: p.read_bytes() for root in f for p in root.rglob("*") if p.is_file()}
    result = execute(tmp_path, f)
    assert result["publication_eligible"] is False
    pending = [c for c in result["cases"] if c["scope_registered"]]
    assert len(pending) == 1 and pending[0]["stock_code"] == "300114"
    assert pending[0]["status"] == "PENDING_GOVERNED_DISPOSITION"
    assert result["held_added"] == 0 and result["facts_or_grades_changed"] is False
    assert all(p.read_bytes() == data for p, data in before.items())


def test_no_registered_scope_and_no_matching_observation_proves_nonapplicability(tmp_path):
    result = execute(tmp_path, fixture(tmp_path, matching=False))
    assert result["publication_eligible"] is True
    assert all(not r["observations_present"] and not r["scope_registered"] for r in result["cases"])


@pytest.mark.parametrize("kind", ["input-registration", "inherited-registration"])
def test_absent_dwd_observation_cannot_hide_registered_historical_scope(tmp_path, kind):
    result = execute(
        tmp_path,
        fixture(
            tmp_path,
            matching=False,
            registered=kind == "input-registration",
            inherited=kind == "inherited-registration",
        ),
    )
    assert result["publication_eligible"] is False
    assert any(c["scope_registered"] and not c["observations_present"] for c in result["cases"])


@pytest.mark.parametrize(
    "damage",
    [
        "omit-case",
        "new-case",
        "relabel-repaired",
        "expand-held",
        "override",
        "case-override",
        "bool-as-int",
        "nonstring-id",
    ],
)
def test_policy_cannot_skip_diagnostics_or_infer_a_disposition(damage):
    policy = copy.deepcopy(json.loads(POLICY.read_bytes()))
    validate_policy(policy)
    if damage == "omit-case":
        policy["cases"].pop()
    elif damage == "new-case":
        policy["cases"].append(copy.deepcopy(policy["cases"][0]))
    elif damage == "relabel-repaired":
        policy["cases"][0]["disposition"] = "REPAIRED"
    elif damage == "expand-held":
        policy["new_quality_held_allowed"] = 1
    elif damage == "override":
        policy["allow_unresolved"] = True
    elif damage == "case-override":
        policy["cases"][0]["allow_unresolved"] = True
    elif damage == "bool-as-int":
        policy["new_quality_held_allowed"] = False
    else:
        policy["cases"][0]["id"] = []
    with pytest.raises(BoundedError, match="BUILD_HISTORICAL_POLICY_CHANGED_OR_UNGOVERNED"):
        validate_policy(policy)


@pytest.mark.parametrize(
    "damage", ["false-string", "duplicate", "unknown", "partial", "changed-identity"]
)
def test_malformed_inherited_registration_never_hides_the_scope(tmp_path, damage):
    f = fixture(tmp_path, matching=False, inherited=True)
    path = f[0] / "members/previous/manifest.json"
    manifest = json.loads(path.read_bytes())
    gate = manifest["pairing"]["historical_gate"]
    if damage == "false-string":
        gate["cases"][0]["scope_registered"] = "false"
    elif damage == "duplicate":
        gate["cases"][0] = copy.deepcopy(gate["cases"][1])
    elif damage == "unknown":
        gate["cases"][0]["id"] = "unknown"
    elif damage == "partial":
        gate["cases"].pop()
    if damage != "changed-identity":
        gate["identity"] = identity({k: v for k, v in gate.items() if k != "identity"})
    else:
        gate["identity"] = "sha256:" + "0" * 64
    save(path, manifest)
    with pytest.raises(BoundedError, match="BUILD_HISTORICAL_INHERITED_REGISTRY_INVALID"):
        execute(tmp_path, f)
