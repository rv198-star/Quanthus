"""Exact fail-closed tests for the Base 2.4 bounded 5m limitation policy."""

import copy
import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22 import canonical, file_sha
from quanthus.pipelines.securities_warehouse.base24_native5m_bounded import (
    Base24BoundedLimitationError,
    validate_bounded_policy,
    validate_bounded_predecessor,
)

POLICY = Path("config/pipelines/base24-native5m-bounded-limitations.v1.json")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    policy = json.loads(POLICY.read_bytes())
    root = tmp_path / "ledger"
    root.mkdir()
    remaining = []
    ledger_rows = []
    for row in [*policy["semantic_qualifications"], *policy["unavailable_security_days"]]:
        remaining.append(
            {
                "venue": row["venue"],
                "stock_code": row["stock_code"],
                "trade_date": row["trade_date"],
                "reason_counts": {row["predecessor_reason"]: 1},
            }
        )
        ledger_rows.append(
            {
                "venue": row["venue"],
                "stock_code": row["stock_code"],
                "trade_date": date.fromisoformat(row["trade_date"]),
                "disposition": "UNRESOLVED",
                "rating": "R3",
                "reason": "missing_despite_tradable_status",
            }
        )
    _write(root / "remaining-r3.safe.json", remaining)
    pq.write_table(pa.Table.from_pylist(ledger_rows), root / "coverage-ledger.parquet")
    _write(root / "manifest.json", {"fixture": True})
    _write(
        root / "result.safe.json",
        {
            "required_security_days": 7804753,
            "observed_R2_security_days": 7783878,
            "justified_no_data_security_days": 20871,
            "R3_security_days": 4,
        },
    )
    daily, dual = tmp_path / "daily.json", tmp_path / "dual.json"
    _write(daily, {"fixture": "daily"})
    _write(dual, {"fixture": "dual"})
    policy["predecessor"]["ledger_manifest_sha256"] = file_sha(root / "manifest.json")
    policy["predecessor"]["remaining_r3_sha256"] = file_sha(root / "remaining-r3.safe.json")
    policy["evidence"]["daily_arbitration_sha256"] = file_sha(daily)
    policy["evidence"]["dual_grid_aggregation_sha256"] = file_sha(dual)
    policy_path = tmp_path / "policy.json"
    _write(policy_path, policy)
    return policy_path, root, daily, dual


def test_exact_bounded_four_are_accepted_without_one_minute(tmp_path):
    policy, root, daily, dual = _fixture(tmp_path)
    receipt = validate_bounded_predecessor(
        policy_path=policy,
        ledger_root=root,
        daily_arbitration_path=daily,
        dual_grid_path=dual,
    )
    assert receipt["status"] == "PASS_WITH_BOUNDED_5M_LIMITATIONS"
    assert receipt["semantic_qualified_security_days"] == 2
    assert receipt["bounded_unavailable_5m_security_days"] == 2
    assert receipt["daily_frequency_unavailable_security_days"] == 0
    assert receipt["one_minute_included"] is False


@pytest.mark.parametrize("mutation", ["extra", "wrong_date", "wrong_reason"])
def test_changed_predecessor_set_or_reason_fails_closed(tmp_path, mutation):
    policy, root, daily, dual = _fixture(tmp_path)
    remaining = json.loads((root / "remaining-r3.safe.json").read_bytes())
    if mutation == "extra":
        remaining.append(
            {
                "venue": "SSE",
                "stock_code": "600000",
                "trade_date": "2020-01-02",
                "reason_counts": {"DAILY_PRICE_BASIS_MISMATCH": 1},
            }
        )
    elif mutation == "wrong_date":
        remaining[0]["trade_date"] = "2023-02-09"
    else:
        remaining[0]["reason_counts"] = {"DAILY_PRICE_BASIS_MISMATCH": 1}
    _write(root / "remaining-r3.safe.json", remaining)
    raw = json.loads(policy.read_bytes())
    raw["predecessor"]["remaining_r3_sha256"] = file_sha(root / "remaining-r3.safe.json")
    _write(policy, raw)
    with pytest.raises(Base24BoundedLimitationError):
        validate_bounded_predecessor(
            policy_path=policy, ledger_root=root, daily_arbitration_path=daily, dual_grid_path=dual
        )


def test_evidence_hash_change_fails_closed(tmp_path):
    policy, root, daily, dual = _fixture(tmp_path)
    daily.write_bytes(b"changed")
    with pytest.raises(Base24BoundedLimitationError, match="EVIDENCE_CHANGED"):
        validate_bounded_predecessor(
            policy_path=policy, ledger_root=root, daily_arbitration_path=daily, dual_grid_path=dual
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("required_intervals", ["1d", "5m", "1m"]),
        ("global_quality_thresholds_changed", True),
    ],
)
def test_one_minute_or_global_relaxation_is_rejected(tmp_path, field, value):
    raw = copy.deepcopy(json.loads(POLICY.read_bytes()))
    raw[field] = value
    path = tmp_path / "policy.json"
    _write(path, raw)
    with pytest.raises(Base24BoundedLimitationError, match="POLICY_INVALID"):
        validate_bounded_policy(path)


def test_unavailable_keys_remain_exact_5m_fail_closed(tmp_path):
    raw = json.loads(POLICY.read_bytes())
    assert all(row["five_minute_fail_closed"] for row in raw["unavailable_security_days"])
    assert all(row["daily_frequency_available"] for row in raw["unavailable_security_days"])
    raw["unavailable_security_days"][0]["five_minute_fail_closed"] = False
    path = tmp_path / "policy.json"
    _write(path, raw)
    with pytest.raises(Base24BoundedLimitationError, match="UNAVAILABLE_RULE_INVALID"):
        validate_bounded_policy(path)
