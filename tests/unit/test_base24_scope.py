"""Exact optional 1m ownership, including shared evidence, with no real payload."""

import hashlib
import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base24_scope import (
    Base24ScopeError,
    select_default_members,
)

POLICY = Path("config/pipelines/base24-default-frequency.v1.json").read_bytes()


def documents():
    def member(path, **extra):
        return dict(path=path, sha256="sha256:" + "0" * 64, size_bytes=3, **extra)

    seed = json.dumps(
        dict(
            content=dict(
                files=[
                    member("payload/one.parquet", dataset_id="minute_1m_research_archive"),
                    member("payload/five.parquet", dataset_id="minute_5m_formal_archive"),
                ]
            )
        )
    ).encode()
    seed_sha = hashlib.sha256(seed).hexdigest()
    files = [
        member(p, source_id=seed_sha, role="historical_seed")
        for p in ("payload/one.parquet", "payload/five.parquet")
    ]
    files += [
        member("payload/new/" + n, source_id="1" * 64, role="intraday_1m")
        for n in (
            "intraday_1m_bars.parquet",
            "after_hours_fixed_price_trades.parquet",
            "ref_trading_calendar.parquet",
        )
    ]
    monthly = dict(
        content=dict(
            files=files,
            source_inventory=[dict(role="historical_seed", manifest_sha256="sha256:" + seed_sha)],
        )
    )
    return monthly, seed


def test_default_excludes_only_optional_bars_and_preserves_shared_sources():
    monthly, seed = documents()
    before = json.dumps(monthly).encode()
    plan = select_default_members(before, seed, POLICY)
    assert {m["path"] for m in plan["excluded"]} == {
        "payload/one.parquet",
        "payload/new/intraday_1m_bars.parquet",
    }
    assert {m["path"] for m in plan["included"]} == {
        "payload/five.parquet",
        "payload/new/after_hours_fixed_price_trades.parquet",
        "payload/new/ref_trading_calendar.parquet",
    }
    assert plan["excluded_logical_bytes"] == 6
    assert plan["source_qualification"] == "NOT_GRANTED"
    assert json.dumps(monthly).encode() == before


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("unknown", "UNCLASSIFIED"),
        ("duplicate", "DUPLICATE"),
        ("seed", "SEED_IDENTITY"),
        ("member", "MEMBER_IDENTITY"),
        ("path", "INVALID_OR_DUPLICATE"),
    ],
)
def test_unknown_ownership_and_identity_drift_fail_closed(defect, reason):
    monthly, seed = documents()
    if defect == "unknown":
        monthly["content"]["files"][-1]["path"] = "payload/new/unknown.parquet"
    elif defect == "duplicate":
        monthly["content"]["files"].append(monthly["content"]["files"][0])
    elif defect == "seed":
        seed += b" "
    elif defect == "member":
        monthly["content"]["files"][0]["size_bytes"] = 4
    else:
        monthly["content"]["files"][0]["path"] = "../outside"
    with pytest.raises(Base24ScopeError, match=reason):
        select_default_members(json.dumps(monthly).encode(), seed, POLICY)


def test_local_governance_metadata_keeps_unknown_supplier_identity():
    monthly, seed = documents()
    monthly["content"]["files"].append(
        dict(
            path="payload/source-plan/MATERIALIZATION.json",
            role="local_governance_evidence",
            sha256="sha256:" + "2" * 64,
            size_bytes=4,
        )
    )
    plan = select_default_members(json.dumps(monthly).encode(), seed, POLICY)
    actual = next(m for m in plan["included"] if m["role"] == "local_governance_evidence")
    assert "source_id" not in actual
    assert actual["size_bytes"] == 4


def test_missing_supplier_identity_cannot_be_treated_as_governance_metadata():
    monthly, seed = documents()
    del monthly["content"]["files"][-1]["source_id"]
    with pytest.raises(Base24ScopeError, match="SOURCE_IDENTITY_MISSING"):
        select_default_members(json.dumps(monthly).encode(), seed, POLICY)
