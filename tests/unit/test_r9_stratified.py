import hashlib
import itertools
import json
from datetime import date, timedelta

import pytest

from quanthus.r9_qualification import check_roles, qualify_pool
from quanthus.r9_stratified import GROUPS, order, r0_support, select_qualified
from quanthus.stage_learning.order_reference_protocol import PROTOCOL_DIGEST
from quanthus.stage_learning.order_rule_screen import evaluate_case, load_rule
from quanthus.structure_v2_inputs import canonical, digest


def primary(e="0.1", w="0.1", d="-0.3", c="0.02"):
    return {
        k: {"value": v, "reason": "available", "revision_id": "sha256:" + "1" * 64}
        for k, v in zip(
            ("directional_efficiency", "range_width", "width_change", "center_shift"),
            (e, w, d, c),
            strict=True,
        )
    }


def test_sampling_proxy_matches_original_frozen_r0_at_boundaries():
    rule = load_rule()
    for values in itertools.product(
        ["-0.33", "0.33", "0.34"],
        ["0.1", "0.18", "0.19"],
        ["-0.3", "-0.2", "-0.19"],
        ["-0.04", "0.04", "0.05"],
    ):
        p = primary(*values)
        case = {
            "protocol_digest": PROTOCOL_DIGEST,
            "primary_window_sessions": 20,
            "recent_window_sessions": 10,
            "knowledge_view": "retrospective",
            "market_date": "2020-07-31",
            "primary_available": True,
            "primary": p,
            "recent": {},
        }
        case["case_id"] = digest(case)
        assert r0_support(p, rule) == evaluate_case(case)["assertions"]["stage_2"]


def pool():
    rows = []
    for role, anchor, _ in GROUPS:
        group = []
        end = date.fromisoformat(anchor)
        for _ in range(256):
            i = len(rows) + len(group)
            identity = {
                "company_id": f"c{i}",
                "security_id": f"s{i}",
                "listing_id": f"l{i}",
                "sample_id": f"sample{i}",
                "episode_id": f"episode{i}",
                "role": role,
                "anchor": anchor,
                "sessions": [
                    {"date": str(end - timedelta(days=39 - j)), "session_id": f"{anchor}/{j}"}
                    for j in range(40)
                ],
            }
            group.append(
                {
                    "identity": identity,
                    "available": True,
                    "reason": "AVAILABLE",
                    "primary_facts": primary() if i % 2 else primary(e="0.8"),
                    "features": [0.1, 0.1, -0.3, 0.02, 0.5, 0.5, 0.0],
                    "facts_digest": "sha256:" + "2" * 64,
                    "packet": {"case_id": identity["sample_id"], "available": True},
                }
            )
        rows.extend(sorted(group, key=lambda r: order(r["identity"])))
    return rows


def test_fixed_strata_choose64_without_reference_labels(tmp_path):
    rows = pool()
    path = tmp_path / "pool.json"
    path.write_text(json.dumps({"rows": rows}))
    out = tmp_path / "selected"
    result = select_qualified(
        __import__("pathlib").Path("."),
        path,
        out,
        expected_pool_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_identity_sha256=hashlib.sha256(
            canonical(
                {"schema": "quanthus.r9-role-identities.v1", "rows": [r["identity"] for r in rows]}
            )
            + b"\n"
        ).hexdigest(),
    )
    assert result["status"] == "SOURCE_STRATA_READY"
    selected = json.loads((out / "qualified-cases.private.json").read_bytes())["rows"]
    assert len(selected) == 64
    assert sum(r["sampling_r0_support"] for r in selected) == 32
    assert all("label" not in r for r in selected)
    check_roles(json.loads((out / "role-identities.private.json").read_bytes()))


def test_stratum_shortage_stops_with_full_ledger_without_borrowing(tmp_path):
    rows = pool()
    for row in rows[512:768]:
        row["primary_facts"] = primary(e="0.8")
    path = tmp_path / "pool.json"
    path.write_text(json.dumps({"rows": rows}))
    out = tmp_path / "selected"
    result = select_qualified(
        __import__("pathlib").Path("."),
        path,
        out,
        expected_pool_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_identity_sha256=hashlib.sha256(
            canonical(
                {"schema": "quanthus.r9-role-identities.v1", "rows": [r["identity"] for r in rows]}
            )
            + b"\n"
        ).hexdigest(),
    )
    assert result["status"] == "STRATUM_CAPACITY_STOP"
    assert len(json.loads((out / "strata-ledger.private.json").read_bytes())["rows"]) == 1536
    assert not (out / "qualified-cases.private.json").exists()


def test_pool_entry_does_not_loosen_original64_contract(tmp_path):
    rows = pool()
    with pytest.raises(ValueError, match="ROLE_DENOMINATOR"):
        check_roles({"rows": [r["identity"] for r in rows]})
    identities = tmp_path / "identities.json"
    identities.write_text(json.dumps({"rows": rows[:64]}))
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": "quanthus.r9-stratified-chain.v1",
                "fixed_role_identity": {
                    "sha256": hashlib.sha256(identities.read_bytes()).hexdigest()
                },
            }
        )
    )
    with pytest.raises(ValueError, match="FIXED_POOL"):
        qualify_pool(
            tmp_path, tmp_path / "missing_source", identities, request, tmp_path / "output"
        )
