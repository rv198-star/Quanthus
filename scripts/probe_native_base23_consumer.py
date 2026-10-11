#!/usr/bin/env python3
"""Read-only candidate-image input probe; never create or execute a research Job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("api", "event-worker", "replay-worker"), required=True)
    args = parser.parse_args()
    from quanthus.platform.data_runtime import event_source_policy_path, replay_provider_path
    from quanthus.policy import find_project_root

    project = find_project_root()
    provider_path = replay_provider_path()
    policy_path = event_source_policy_path()
    if provider_path != "config/replay-data-provider.base23.v1.json":
        raise RuntimeError("candidate image did not select the native provider")
    if policy_path != "config/p4-event-scan-source.base23.v1.json":
        raise RuntimeError("candidate image did not select the native event policy")
    receipt = json.loads(
        (project / "config/p4-direct-dwd-canonical-projection.base23.v1.json").read_text()
    )
    result = {
        "schema_version": "quanthus.base23-consumer-image-probe.v1",
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": args.kind,
        "provider_config": provider_path,
        "event_source_policy": policy_path,
        "canonical_bundle_digest": receipt["canonical_bundle_digest"],
        "research_job_executed": False,
        "legacy_base_mounted": Path(
            "/srv/quanthus/dwd-dws-v2.1/base/dwd-base-202607-v2.1"
        ).exists(),
    }
    if result["legacy_base_mounted"]:
        raise RuntimeError("candidate image still exposes the retired Base")
    if args.kind == "api":
        import quanthus.platform.api  # noqa: F401
    else:
        import duckdb

        expected_versions = {
            value["logical_digest"]["duckdb_version"]
            for value in receipt["datasets"].values()
            if value["logical_digest"] is not None
        }
        if expected_versions != {"v" + duckdb.__version__}:
            raise RuntimeError("consumer DuckDB differs from the qualified canonical engine")
        result["duckdb_version"] = duckdb.__version__

        from quanthus.backtest.data_provider import load_replay_data_provider_config
        from quanthus.backtest.native_dwd_source import physical_paths
        from quanthus.event_library.source import load_event_scan_source_policy
        from quanthus.pipelines.securities_warehouse.native_base23_reader import read_native_tables

        config = load_replay_data_provider_config(project / provider_path)
        load_event_scan_source_policy(
            project / policy_path,
            release_path=project / "config/releases/base23-oci-consumers.v1.json",
            provider_path=project / provider_path,
        )
        release = json.loads((project / "config/releases/base23-oci-consumers.v1.json").read_text())
        native = read_native_tables(config.dwd_root, release, ("dwd_listing",))
        for path in (config.dwd_root, config.correction_root):
            if not os.statvfs(path).f_flag & os.ST_RDONLY:
                raise RuntimeError("candidate source mount is not read-only")
        continuation = config.materialization["native_base23"]["continuation"]
        continuation_path = config.correction_root.parent / continuation["path"]
        actual = "sha256:" + hashlib.sha256(continuation_path.read_bytes()).hexdigest()
        if actual != continuation["sha256"]:
            raise RuntimeError("candidate correction continuation differs")
        connection = duckdb.connect(":memory:")
        try:
            connection.execute("SET memory_limit='512MB'")
            connection.execute("SET threads=1")
            rows = connection.execute(
                "SELECT listing.listing_code, "
                "CAST(CAST(bar.observation_start AS DATE) AS VARCHAR), "
                "CAST(bar.close AS VARCHAR), CAST(bar.knowledge_from AS VARCHAR) "
                "FROM read_parquet(?) bar JOIN read_parquet(?) listing USING (listing_id) "
                "WHERE listing.listing_code IN ('000919','600805','600810') "
                "AND listing.knowledge_to IS NULL AND bar.knowledge_to IS NULL "
                "AND bar.interval_size='1d' AND bar.price_basis='UNADJUSTED' "
                "AND CAST(bar.observation_start AS DATE) "
                "BETWEEN DATE '2019-03-27' AND DATE '2019-03-29' "
                "ORDER BY listing.listing_code, bar.observation_start",
                [
                    [str(p) for p in physical_paths(config.dwd_root, "dwd_security_bar")],
                    [str(p) for p in native.table_paths["dwd_listing"]],
                ],
            ).fetchall()
        finally:
            connection.close()
        if len(rows) != 9 or rows[0][2] != "7.9300000000" or any(x[2] is None for x in rows):
            raise RuntimeError("actual native input delta is absent from the candidate image")
        if args.kind == "event-worker":
            import quanthus.platform.event_worker  # noqa: F401
        else:
            import quanthus.platform.worker  # noqa: F401
        result.update(
            native_binding=dict(native.binding),
            source_mounts_read_only=True,
            correction_continuation_sha256=actual,
            actual_source_rows=[list(row) for row in rows],
        )
    result["status"] = "PASS"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
