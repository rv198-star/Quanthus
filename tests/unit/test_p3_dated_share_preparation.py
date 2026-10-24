"""Whole raw before-images and streamed physical history are acceptance inputs."""

from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.expert_strategy.p3_dated_share_preparation import (
    compare_healthy_features,
    replace_raw_population,
    write_feature_replacement,
)


def row(code, day=1):
    return SimpleNamespace(security_id=code, listing_id=code, signal_date=date(2019, 1, day))


def test_raw_merge_keeps_every_unselected_candidate_not_just_retained_candidates():
    a, b, c = row("affected"), row("healthy"), row("healthy", 2)
    old = SimpleNamespace(
        security_ids=("affected", "healthy"),
        raw_candidates=(a, b, c),
        reason_counts={"not_triggered": 10},
        evaluated_rows=100,
        series_count=2,
        decoded_rows=120,
    )
    before = ([a], Counter(not_triggered=3), 40, 1, 50)
    after = ([], Counter(not_triggered=4), 40, 1, 50)
    raw, reasons, evaluated, series, decoded = replace_raw_population(
        old, before, after, ("affected",)
    )
    assert raw == [b, c]
    assert reasons == {"not_triggered": 11}
    assert (evaluated, series, decoded) == (100, 2, 120)


@pytest.mark.parametrize(
    "mutation", ["missing-before", "changed-before", "negative-reason", "unknown-id"]
)
def test_incomplete_or_altered_before_census_is_never_a_reuse_hit(mutation):
    a = row("affected")
    old = SimpleNamespace(
        security_ids=("affected",),
        raw_candidates=(a,),
        reason_counts={"not_triggered": 3},
        evaluated_rows=40,
        series_count=1,
        decoded_rows=50,
    )
    before = ([a], Counter(not_triggered=3), 40, 1, 50)
    selected = ("affected",)
    if mutation == "missing-before":
        before = ([], *before[1:])
    if mutation == "changed-before":
        before = ([row("affected", 2)], *before[1:])
    if mutation == "negative-reason":
        before = (before[0], Counter(not_triggered=4), *before[2:])
    if mutation == "unknown-id":
        selected = ("unknown",)
    with pytest.raises(ValueError, match="BEFORE_IMAGE"):
        replace_raw_population(old, before, before, selected)


@pytest.fixture
def features(tmp_path):
    original, target = tmp_path / "old.parquet", tmp_path / "new.parquet"
    table = pa.table(
        {
            "security_id": ["a", "b", "b", "c"],
            "market_date": [date(2019, 1, 1), date(2019, 1, 1), date(2019, 1, 2), date(2019, 1, 1)],
            "exact": pa.array([Decimal("1.23000000001")] * 4, type=pa.decimal128(24, 11)),
            "known_at": pa.array(
                [datetime(2026, 10, 24, 18, tzinfo=timezone.utc)] * 4,
                type=pa.timestamp("us", tz="UTC"),
            ),
            "unavailable": pa.array([None] * 4, type=pa.timestamp("us", tz="UTC")),
        }
    )
    pq.write_table(table, original, row_group_size=1)
    rebuilt = table.slice(1, 2)
    rebuilt = rebuilt.set_column(
        2,
        table.schema.field(2),
        pa.array([Decimal("2.34000000002")] * 2, type=pa.decimal128(24, 11)),
    )
    return original, target, table, rebuilt


def test_bounded_replacement_preserves_exact_decimal_times_and_all_healthy_values(features):
    source, target, before, rebuilt = features
    original_bytes = source.read_bytes()
    assert write_feature_replacement(source, target, rebuilt, ("b",)) == 2
    assert compare_healthy_features(source, target, ("b",)) == 2
    assert source.read_bytes() == original_bytes
    out = pq.read_table(target)
    assert out.slice(1, 2).equals(rebuilt, check_metadata=True)
    assert out["known_at"].equals(before["known_at"])
    assert out["unavailable"].null_count == 4


@pytest.mark.parametrize("mutation", ["missing", "extra", "coordinate", "schema", "unauthorized"])
def test_bad_affected_partition_is_not_patched_into_healthy_history(features, mutation):
    source, target, table, rebuilt = features
    if mutation == "missing":
        rebuilt = rebuilt.slice(0, 1)
    if mutation == "extra":
        rebuilt = pa.concat_tables([rebuilt, rebuilt.slice(0, 1)])
    if mutation == "coordinate":
        rebuilt = rebuilt.set_column(
            1, table.schema.field(1), pa.array([date(2019, 2, 1), date(2019, 2, 2)])
        )
    if mutation == "schema":
        rebuilt = rebuilt.drop(["known_at"])
    if mutation == "unauthorized":
        rebuilt = table.slice(0, 1)
    with pytest.raises(ValueError, match="DATED_FEATURE"):
        write_feature_replacement(source, target, rebuilt, ("b",))


def test_resealed_healthy_value_corruption_fails_full_streamed_comparison(features):
    source, target, table, rebuilt = features
    write_feature_replacement(source, target, rebuilt, ("b",))
    t = pq.read_table(target)
    pq.write_table(
        t.set_column(
            2,
            t.schema.field(2),
            pa.array(
                [Decimal("9.99000000001"), *t["exact"].to_pylist()[1:]], type=pa.decimal128(24, 11)
            ),
        ),
        target,
    )
    with pytest.raises(ValueError, match="HEALTHY_VALUE_CHANGED"):
        compare_healthy_features(source, target, ("b",))
