"""Replace complete affected-security prerequisites from each role's own census."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quanthus.backtest.baseline_research import (
    _create_feature_source_views,
    _feature_panel_query,
    _validate_feature_sources,
)
from quanthus.event_library.scan import CandidateScanShard
from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_dated_share_successor as s
from quanthus.expert_strategy import p3_historical_action_successor as old_module
from quanthus.expert_strategy import p3_historical_robustness as h
from quanthus.pipelines.securities_warehouse import p10_dated_distribution_input as p10


def replace_raw_population(old: Any, before: Any, after: Any, selected: tuple[str, ...]) -> Any:
    """Require exact affected before-images and a full raw census, never retained rows."""
    if not set(selected) <= set(old.security_ids) or tuple(
        r for r in old.raw_candidates if r.security_id in selected
    ) != tuple(before[0]):
        raise ValueError("DATED_PREPARATION_RAW_BEFORE_IMAGE")
    reasons = Counter(old.reason_counts)
    reasons.subtract(before[1])
    if any(n < 0 for n in reasons.values()):
        raise ValueError("DATED_PREPARATION_REASON_BEFORE_IMAGE")
    reasons.update(after[1])
    raw = [r for r in old.raw_candidates if r.security_id not in selected] + list(after[0])
    raw.sort(key=lambda r: (r.security_id, r.listing_id, r.signal_date))
    return (
        raw,
        +reasons,
        old.evaluated_rows - before[2] + after[2],
        (old.series_count - before[3] + after[3]),
        old.decoded_rows - before[4] + after[4],
    )


def compare_healthy_features(original: Path, target: Path, selected: tuple[str, ...]) -> int:
    """Full value equality in bounded aligned batches, without a giant SQL sort."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    a, b = pq.ParquetFile(original), pq.ParquetFile(target)
    if a.schema_arrow != b.schema_arrow or a.metadata.num_rows != b.metadata.num_rows:
        raise ValueError("DATED_PREPARATION_FEATURE_SCHEMA_OR_COUNT")
    total = 0
    for x, y in zip(
        a.iter_batches(batch_size=65536), b.iter_batches(batch_size=65536), strict=True
    ):
        left, right = pa.Table.from_batches([x]), pa.Table.from_batches([y])
        mask = pc.invert(pc.is_in(left["security_id"], value_set=pa.array(selected)))
        if not left["security_id"].equals(right["security_id"]):
            raise ValueError("DATED_PREPARATION_FEATURE_COORDINATES")
        if not left.filter(mask).equals(right.filter(mask), check_metadata=True):
            raise ValueError("DATED_PREPARATION_HEALTHY_VALUE_CHANGED")
        total += len(left.filter(mask))
    return total


def write_feature_replacement(
    original: Path, target: Path, rebuilt: Any, selected: tuple[str, ...]
) -> int:
    """Stream physical history once; replace only identical complete coordinates."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if target.exists() or target.is_symlink():
        raise ValueError("DATED_FEATURE_OUTPUT_EXISTS")
    source = pq.ParquetFile(original)
    if rebuilt.schema != source.schema_arrow:
        raise ValueError("DATED_FEATURE_REBUILD_SCHEMA")
    coordinate_names = ["security_id", "market_date"]
    if any(sid not in selected for sid in rebuilt["security_id"].to_pylist()):
        raise ValueError("DATED_FEATURE_UNAUTHORIZED_SECURITY")
    cursor = 0
    with pq.ParquetWriter(target, source.schema_arrow, compression="zstd") as writer:
        for batch in source.iter_batches(batch_size=65536):
            table = pa.Table.from_batches([batch])
            indices = pc.indices_nonzero(
                pc.is_in(table["security_id"], value_set=pa.array(selected))
            ).to_pylist()
            pieces: list[Any] = []
            start = 0
            for _, group in groupby(enumerate(indices), lambda pair: pair[1] - pair[0]):
                positions = [p for _, p in group]
                a, end = positions[0], positions[-1] + 1
                replacement = rebuilt.slice(cursor, end - a)
                if (
                    not table.slice(a, end - a)
                    .select(coordinate_names)
                    .equals(replacement.select(coordinate_names))
                ):
                    raise ValueError("DATED_FEATURE_REBUILD_COORDINATES")
                pieces.extend((table.slice(start, a - start), replacement))
                start, cursor = end, cursor + end - a
            pieces.append(table.slice(start))
            writer.write_table(pa.concat_tables(pieces), row_group_size=50000)
    if cursor != len(rebuilt):
        raise ValueError("DATED_FEATURE_INCOMPLETE_SECURITY")
    return cursor


def prepare(
    repository: Path, source: Path, old_prepared: Path, output: Path, request: dict[str, Any]
) -> dict[str, Any]:
    import duckdb
    import pyarrow as pa

    sha = native._sha
    if request != s.load_request(repository) or output.exists() or output.is_symlink():
        raise ValueError("DATED_PREPARATION_REQUEST_OR_OUTPUT")
    if (
        sha(old_prepared / "preparation.json") != request["prior_preparation_sha256"]
        or sha(old_prepared / "features.parquet") != request["prior_feature_sha256"]
    ):
        raise ValueError("DATED_PREPARATION_PARENT_IDENTITY")
    old = h.read_preparation(old_prepared / "preparation.json")
    if (
        old.get("action_successor_request_digest")
        != digest_value(old_module.load_request(repository))
        or sha(old_prepared / "raw-candidate-census.json") != old["raw_candidate_census_sha256"]
    ):
        raise ValueError("DATED_PREPARATION_COMPLETE_RAW_CLOSURE")
    raw = CandidateScanShard.from_dict(
        json.loads((old_prepared / "raw-candidate-census.json").read_bytes())
    )
    bundle, facts = p10.load_input(
        source, request["source_manifest_sha256"].removeprefix("sha256:")
    )
    selected = tuple(sorted({f.security_code for f in facts if f.share_listing_date != f.ex_date}))
    if selected != tuple(request["affected_security_ids"]):
        raise ValueError("DATED_PREPARATION_AFFECTED_IDS")
    # The typed loader proves a byte-identical complete parent, one qualified
    # key rewrite, and no other semantic input change. All parent code/config
    # members remain hash-validated by load_request above.
    output.mkdir()
    (output / "spill").mkdir()
    c = duckdb.connect(
        config={
            "memory_limit": "4GB",
            "threads": 2,
            "max_temp_directory_size": "6GB",
            "temp_directory": str(output / "spill"),
        }
    )
    c.execute("SET timezone='UTC'")
    try:
        paths = {
            d.dataset: bundle.historical_bundle.partition_paths(d.dataset)
            for d in bundle.historical_bundle.datasets
        }
        feature_names = {
            "daily_ohlcv_none",
            "daily_universe",
            "price_limit_execution_daily",
            "security_code_validity",
            "corporate_actions_normalized",
            "corporate_action_exceptions",
        }
        _create_feature_source_views(
            c,
            {k: v for k, v in paths.items() if k in feature_names},
            None,
            scope_end=date(2021, 12, 31),
        )
        literals = ",".join("'" + x.replace("'", "''") + "'" for x in selected)
        for name in feature_names:
            members = ",".join("'" + str(p).replace("'", "''") + "'" for p in paths[name])
            c.execute(
                f"CREATE OR REPLACE VIEW source_{name} AS SELECT * "
                f"FROM read_parquet([{members}]) WHERE security_id IN ({literals})"
            )
        for name in ("corporate_actions_normalized", "corporate_action_exceptions"):
            c.execute(f"CREATE VIEW source_{name}_mapped AS SELECT * FROM source_{name}")
        _validate_feature_sources(c)
        with s.DatedShareReader(
            bundle, facts, datetime.fromisoformat(request["source_information_cutoff"])
        ) as reader:
            mapping = reader.load_industry_mapping(
                expected_version="static-level1-concentration-research-v1"
            )
        dates = tuple(
            r[0]
            for r in c.execute(
                "SELECT trade_date FROM read_parquet(?) "
                "WHERE exchange IN ('SSE','SZSE') AND is_open=1 GROUP BY trade_date "
                "HAVING count(DISTINCT exchange)=2 ORDER BY trade_date",
                [str(paths["trading_calendar"][0])],
            ).fetchall()
        )
        c.execute(
            "CREATE TEMP TABLE selected_universe_sessions AS SELECT security_id,market_date,"
            "row_number() OVER(PARTITION BY security_id ORDER BY market_date) "
            "AS listing_sessions FROM source_daily_universe"
        )
        previous_facts = tuple(f for f in facts if f.security_code not in selected)
        c.register(
            "old_added_arrow",
            pa.Table.from_pylist(
                old_module._action_rows(previous_facts, old["effective_source_manifest_sha256"])
            ),
        )
        c.execute("CREATE TEMP TABLE new_actions AS SELECT * FROM old_added_arrow")
        c.unregister("old_added_arrow")
        before = old_module._scan_raw_candidates(
            c,
            mapping,
            old["source_information_cutoff"],
            repository,
            dates=dates,
            added=True,
            security_ids=selected,
        )
        c.register(
            "all_added_arrow",
            pa.Table.from_pylist(old_module._action_rows(facts, bundle.manifest_sha256)),
        )
        c.execute("CREATE OR REPLACE TEMP TABLE new_actions AS SELECT * FROM all_added_arrow")
        c.unregister("all_added_arrow")
        after = old_module._scan_raw_candidates(
            c,
            mapping,
            request["source_information_cutoff"],
            repository,
            dates=dates,
            added=True,
            security_ids=selected,
        )
        combined = replace_raw_population(raw, before, after, selected)
        c.execute(
            "CREATE OR REPLACE VIEW source_corporate_actions_normalized_mapped AS "
            "SELECT security_id,effective_date,known_at,cash_dividend_per_share,bonus_share_ratio "
            "FROM source_corporate_actions_normalized UNION ALL SELECT security_id,effective_date,"
            "known_at,cash_dividend_per_share,bonus_share_ratio FROM new_actions"
        )
        query = _feature_panel_query(
            date(2011, 1, 4),
            date(2021, 12, 31),
            output_order="security-date",
            continuity_asof_each_row=True,
            retrospective_action_cutoff=datetime.fromisoformat(
                request["source_information_cutoff"]
            ),
        )
        rebuilt = c.execute(query).fetch_arrow_table()
        target = str(output / "features.parquet").replace("'", "''")
        replaced_rows = write_feature_replacement(
            old_prepared / "features.parquet", output / "features.parquet", rebuilt, selected
        )
        healthy_rows = compare_healthy_features(
            old_prepared / "features.parquet", output / "features.parquet", selected
        )
        census = c.execute(
            "SELECT count(*),count(*) FILTER(WHERE feature_ready),"
            "count(*) FILTER(WHERE entry_eligible),count(*) FILTER(WHERE exception_active),"
            "count(*) FILTER(WHERE unexplained_price_transition),"
            "count(DISTINCT security_id) FILTER(WHERE scope_continuity_excluded) "
            f"FROM read_parquet('{target}')"
        ).fetchone()
        assert census is not None
        report = dict(old["feature_report"])
        for k, v in zip(
            (
                "row_count",
                "feature_ready_count",
                "entry_eligible_count",
                "exception_active_count",
                "unexplained_transition_count",
                "continuity_excluded_security_count",
            ),
            census,
            strict=True,
        ):
            report[k] = v
        report.update(
            data_content_digest=bundle.bundle_content_digest,
            data_manifest_sha256=bundle.manifest_sha256,
            bundle_snapshot_id=bundle.historical_bundle.snapshot.snapshot_id,
            bundle_digest=bundle.bundle_content_digest,
        )
        population = old_module._finalize_population(
            combined[0],
            combined[1],
            combined[2],
            combined[3],
            combined[4],
            SimpleNamespace(
                by_security_id={sid: mapping.by_security_id.get(sid) for sid in raw.security_ids}
            ),
            repository,
            dates=dates,
            output=output,
        )
        proof = {
            "affected_security_ids": list(selected),
            "healthy_feature_rows_compared": healthy_rows,
            "affected_feature_rows_rebuilt": replaced_rows,
            "unchanged_feature_rows_exact_difference": 0,
            "complete_raw_census_before_image_verified": True,
            "full_global_candidate_rebuild": True,
            "global_retain_once": True,
            "full_security_rescan": False,
            "prior_parent_input_byte_identical": True,
            "own_prior_raw_sha256": sha(old_prepared / "raw-candidate-census.json"),
            "own_prior_preparation_sha256": sha(old_prepared / "preparation.json"),
            "own_prior_feature_sha256": sha(old_prepared / "features.parquet"),
            "affected_raw_before": len(before[0]),
            "affected_raw_after": len(after[0]),
            "qualified_source_manifest_sha256": bundle.manifest_sha256,
            "baseline_reuse": "NOT_ASSUMED_RERUN_WITH_NEW_FULL_POPULATION",
        }
        result = {
            **old,
            **population,
            "feature_report": report,
            "feature_identity": digest_value(
                {"source": bundle.manifest_sha256, "query": query, "proof": proof}
            ),
            "feature_disposition": "AFFECTED_ID_REBUILT_OWN_HEALTHY_FEATURES_AND_FULL_RAW_REUSED",
            "action_successor_request_digest": digest_value(request),
            "effective_source_manifest_sha256": bundle.manifest_sha256,
            "source_information_cutoff": request["source_information_cutoff"],
            "incremental_proof": proof,
        }
        (output / "preparation.json").write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n"
        )
        return result
    finally:
        c.close()
