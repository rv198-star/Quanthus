from __future__ import annotations

import pytest

from quanthus.pipelines.hist_1d_factor_completion import canonicalize_factors


def test_equal_cross_listing_factor_observations_merge_and_conflicts_fail() -> None:
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    c.execute("""CREATE TABLE factor_candidates AS SELECT 'security' security_id,
        'PROVIDER_ADJUST_FACTOR' adjustment_method,'PROVIDER_NATIVE' adjustment_direction,
        TIMESTAMP '2001-03-02' effective_from,NULL::TIMESTAMP effective_to,
        TIMESTAMP '2026-11-11 00:01:00' knowledge_from,
        'source-a' source_artifact_sha256,'listing-a' source_record_id,2.959735::DECIMAL(38,18)
        factor_value""")
    c.execute("""INSERT INTO factor_candidates SELECT * REPLACE (
        TIMESTAMP '2026-11-11 00:02:00' AS knowledge_from,'source-b' AS source_artifact_sha256,
        'listing-b' AS source_record_id) FROM factor_candidates""")
    result = canonicalize_factors(c).to_pylist()
    assert len(result) == 1
    assert result[0]["source_record_id"] == "listing-a"
    assert result[0]["effective_to"] is None
    c.execute("UPDATE factor_candidates SET factor_value=3 WHERE source_record_id='listing-b'")
    with pytest.raises(ValueError, match="FACTOR_EVENT_VALUE_CONFLICT"):
        canonicalize_factors(c)
    c.close()
