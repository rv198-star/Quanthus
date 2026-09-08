"""CPU-first capacity qualification for the stock-stage historical-learning node.

This module is operational benchmark tooling. It does not classify market stages, settle future
outcomes, train a production model, or open held-out partitions. Real-DWD probes are restricted to
the development dates frozen in the capacity config and emit only resource/timing counts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StageTrainingCapacityError(RuntimeError):
    """Fail-closed error for training-node capacity qualification."""


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StageTrainingCapacityError(f"JSON root must be an object: {path}")
    return value


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise StageTrainingCapacityError(f"{key} must be an object")
    return result


def _required_int(value: Mapping[str, Any], key: str, *, minimum: int = 1) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < minimum:
        raise StageTrainingCapacityError(f"{key} must be an integer >= {minimum}")
    return result


def load_capacity_config(path: Path) -> dict[str, Any]:
    config = _load_json(path)
    if config.get("schema_version") != "quanthus.stage-training-node.v1":
        raise StageTrainingCapacityError("unsupported stage-training node config")
    resource_budget = _required_mapping(config, "resource_budget")
    _required_int(resource_budget, "logical_cpu_threads")
    _required_int(resource_budget, "memory_limit_gib")
    _required_int(resource_budget, "scratch_limit_gib")
    data_source = _required_mapping(config, "data_source")
    if data_source.get("read_guard_required") is not True:
        raise StageTrainingCapacityError("development read guard must remain enabled")
    benchmark = _required_mapping(config, "benchmark")
    real_dwd = _required_mapping(benchmark, "real_dwd")
    years = real_dwd.get("years")
    forbidden = real_dwd.get("forbidden_years")
    if not isinstance(years, list) or not years or any(
        isinstance(year, bool) or not isinstance(year, int) for year in years
    ):
        raise StageTrainingCapacityError("real_dwd.years must be a non-empty integer list")
    if not isinstance(forbidden, list) or any(year in forbidden for year in years):
        raise StageTrainingCapacityError("benchmark years intersect forbidden years")
    if any(year > 2021 for year in years):
        raise StageTrainingCapacityError("capacity benchmark cannot read post-development years")
    return config


def _training_root(config: Mapping[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    data_source = _required_mapping(config, "data_source")
    raw = data_source.get("training_root_default")
    if not isinstance(raw, str) or not raw:
        raise StageTrainingCapacityError("training_root_default is missing")
    return Path(raw).expanduser().resolve()


def _verify_manifest(
    root: Path,
    spec: Mapping[str, Any],
    *,
    label: str,
    check_base_fields: bool,
) -> dict[str, Any]:
    relative = spec.get("relative_path")
    expected_manifest = spec.get("manifest_sha256")
    expected_content = spec.get("content_digest")
    if not isinstance(relative, str) or not isinstance(expected_manifest, str):
        raise StageTrainingCapacityError(f"{label} source contract is incomplete")
    package = root / relative
    if not package.is_dir() or package.is_symlink():
        raise StageTrainingCapacityError(f"{label} package is missing or symlinked: {package}")
    manifest_path = package / "manifest.json"
    ready_path = package / "READY"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise StageTrainingCapacityError(f"{label} manifest/READY is missing")
    observed_manifest = _sha256(manifest_path)
    if observed_manifest != expected_manifest:
        raise StageTrainingCapacityError(
            f"{label} manifest digest differs: {observed_manifest} != {expected_manifest}"
        )
    ready = ready_path.read_text(encoding="utf-8").strip()
    if ready != expected_manifest:
        raise StageTrainingCapacityError(f"{label} READY does not bind its manifest")
    manifest = _load_json(manifest_path)
    if expected_content is not None and manifest.get("content_digest") != expected_content:
        raise StageTrainingCapacityError(f"{label} content digest differs")
    if check_base_fields:
        for field in ("through_date", "table_count", "payload_bytes"):
            if manifest.get(field) != spec.get(field):
                raise StageTrainingCapacityError(f"base {field} differs")
    return {
        "relative_path": relative,
        "manifest_sha256": observed_manifest,
        "content_digest": manifest.get("content_digest"),
    }


def _first_writable_path(root: Path) -> Path | None:
    for directory, directories, files in os.walk(root):
        directory_path = Path(directory)
        for name in [".", *directories, *files]:
            path = directory_path if name == "." else directory_path / name
            try:
                mode = path.stat().st_mode
            except FileNotFoundError:
                continue
            if mode & 0o222:
                return path
    return None


def _resource_snapshot(training_root: Path) -> dict[str, Any]:
    try:
        import psutil  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised by bootstrap before install
        raise StageTrainingCapacityError("psutil is required for capacity qualification") from exc
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = shutil.disk_usage(training_root)
    return {
        "logical_cpus": os.cpu_count(),
        "memory_total_bytes": memory.total,
        "memory_available_bytes": memory.available,
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }


def verify_training_node(
    config: Mapping[str, Any], *, training_root: Path | None = None
) -> dict[str, Any]:
    root = _training_root(config, training_root)
    data_source = _required_mapping(config, "data_source")
    source_relative = data_source.get("source_relative_path")
    if not isinstance(source_relative, str):
        raise StageTrainingCapacityError("source_relative_path is missing")
    source_root = root / source_relative
    if not source_root.is_dir() or source_root.is_symlink():
        raise StageTrainingCapacityError(f"training source root is missing: {source_root}")
    base = _verify_manifest(
        source_root,
        _required_mapping(data_source, "base"),
        label="base",
        check_base_fields=True,
    )
    correction = _verify_manifest(
        source_root,
        _required_mapping(data_source, "correction"),
        label="correction",
        check_base_fields=False,
    )
    if data_source.get("source_must_be_read_only") is True:
        writable = _first_writable_path(source_root)
        if writable is not None:
            raise StageTrainingCapacityError(f"training source is writable: {writable}")

    for directory in ("cache", "runs", "models", "logs", "manifests", "tmp"):
        path = root / directory
        if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
            raise StageTrainingCapacityError(f"training output directory is unavailable: {path}")

    snapshot = _resource_snapshot(root)
    budget = _required_mapping(config, "resource_budget")
    gib = 1024**3
    if snapshot["memory_total_bytes"] < _required_int(budget, "minimum_total_memory_gib") * gib:
        raise StageTrainingCapacityError("training node total memory is below the frozen minimum")
    if snapshot["memory_available_bytes"] < _required_int(
        budget, "minimum_available_memory_gib_before_run"
    ) * gib:
        raise StageTrainingCapacityError(
            "training node available memory is below the frozen minimum"
        )
    if snapshot["disk_free_bytes"] < (
        _required_int(budget, "minimum_free_disk_gib_before_run") * gib
    ):
        raise StageTrainingCapacityError("training node free disk is below the frozen minimum")
    visible_cpus = snapshot["logical_cpus"]
    required_threads = _required_int(budget, "logical_cpu_threads")
    leave_for_host = _required_int(budget, "leave_logical_cpus_for_host", minimum=0)
    if not isinstance(visible_cpus, int) or visible_cpus < required_threads + leave_for_host:
        raise StageTrainingCapacityError(
            "training node CPU budget leaves fewer host CPUs than frozen"
        )
    return {
        "training_root": str(root),
        "source_root": str(source_root),
        "source": {"base": base, "correction": correction},
        "resources": snapshot,
        "resource_budget": dict(budget),
    }


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value * 1024)  # Linux reports KiB.


def _synthetic_benchmark(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    import numpy as np  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.preprocessing import StandardScaler  # type: ignore
    from threadpoolctl import threadpool_limits  # type: ignore

    benchmark = _required_mapping(config, "benchmark")
    rows = benchmark.get("synthetic_rows")
    if not isinstance(rows, list) or any(
        isinstance(n, bool) or not isinstance(n, int) for n in rows
    ):
        raise StageTrainingCapacityError("synthetic_rows is invalid")
    feature_count = _required_int(benchmark, "synthetic_feature_count")
    model_probe = _required_mapping(benchmark, "model_probe")
    r1_candidates = _required_int(model_probe, "r1_vectorized_parameter_candidates")
    threads = _required_int(_required_mapping(config, "resource_budget"), "logical_cpu_threads")
    results: list[dict[str, Any]] = []
    for count in rows:
        rng = np.random.default_rng(20260908 + count)
        start = time.perf_counter()
        matrix = rng.normal(size=(count, feature_count)).astype(np.float32, copy=False)
        create_seconds = time.perf_counter() - start
        noise = rng.normal(scale=0.25, size=count).astype(np.float32, copy=False)
        labels = (
            (matrix[:, 0] + 0.5 * matrix[:, 1] - 0.25 * matrix[:, 2] + noise) > 0
        ).astype(np.int8)

        start = time.perf_counter()
        hits = 0
        for index in range(r1_candidates):
            threshold = -0.45 + index * (0.90 / max(r1_candidates - 1, 1))
            candidate = (matrix[:, 0] + 0.25 * matrix[:, 1]) > threshold
            hits += int(candidate.sum())
        r1_seconds = time.perf_counter() - start
        if hits < 0:  # pragma: no cover - keeps the measured reduction live.
            raise StageTrainingCapacityError("synthetic R1 reduction is invalid")

        start = time.perf_counter()
        with threadpool_limits(limits=threads):
            scaled = StandardScaler().fit_transform(matrix)
            model = LogisticRegression(
                C=1.0,
                max_iter=100,
                solver="lbfgs",
                random_state=20260908,
            )
            model.fit(scaled, labels)
            iterations = int(model.n_iter_[0])
        r2_seconds = time.perf_counter() - start
        results.append(
            {
                "rows": count,
                "features": feature_count,
                "matrix_build_seconds": create_seconds,
                "r1_candidates": r1_candidates,
                "r1_vectorized_seconds": r1_seconds,
                "r2_logistic_seconds": r2_seconds,
                "r2_iterations": iterations,
                "peak_rss_bytes_after_stage": _peak_rss_bytes(),
            }
        )
        del matrix, labels, noise, scaled, model
    return results


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _real_dwd_benchmark(
    config: Mapping[str, Any], *, source_root: Path, training_root: Path
) -> list[dict[str, Any]]:
    import duckdb  # type: ignore

    benchmark = _required_mapping(config, "benchmark")
    real = _required_mapping(benchmark, "real_dwd")
    years = list(real["years"])
    row_limits = real.get("row_limits")
    if not isinstance(row_limits, list) or any(
        isinstance(limit, bool) or not isinstance(limit, int) for limit in row_limits
    ):
        raise StageTrainingCapacityError("real_dwd.row_limits is invalid")
    if any(year > 2021 for year in years):
        raise StageTrainingCapacityError("real DWD benchmark attempted held-out years")

    data_source = _required_mapping(config, "data_source")
    base_spec = _required_mapping(data_source, "base")
    base_relative = base_spec.get("relative_path")
    if not isinstance(base_relative, str):
        raise StageTrainingCapacityError("base relative path is missing")
    table_root = (
        source_root
        / base_relative
        / "payload/security_market_observation/dwd_security_bar"
    )
    if not table_root.is_dir():
        raise StageTrainingCapacityError(f"dwd_security_bar root is missing: {table_root}")
    globs = [
        str(
            table_root
            / "interval_size=1d"
            / f"observation_start_year={year}"
            / "**/*.parquet"
        )
        for year in years
    ]
    for year in years:
        parent = table_root / "interval_size=1d" / f"observation_start_year={year}"
        if not parent.is_dir():
            raise StageTrainingCapacityError(f"approved benchmark year is unavailable: {year}")
        if year > 2021:
            raise StageTrainingCapacityError("post-development partition path was constructed")

    resource_budget = _required_mapping(config, "resource_budget")
    thread_limit = _required_int(resource_budget, "logical_cpu_threads")
    memory_limit = _required_int(resource_budget, "memory_limit_gib")
    path_list = "[" + ",".join(_sql_literal(pattern) for pattern in globs) + "]"
    results: list[dict[str, Any]] = []
    tmp_parent = training_root / "tmp"
    with tempfile.TemporaryDirectory(prefix="stage-capacity-", dir=tmp_parent) as temp_directory:
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(f"SET threads={thread_limit}")
            connection.execute(f"SET memory_limit='{memory_limit}GB'")
            connection.execute("SET preserve_insertion_order=false")
            connection.execute(f"SET temp_directory={_sql_literal(temp_directory)}")
            for limit in row_limits:
                query = f"""
                WITH sampled AS (
                  SELECT listing_id, observation_start, close, volume
                  FROM read_parquet({path_list}, hive_partitioning=true)
                  WHERE interval_size='1d'
                    AND price_basis='UNADJUSTED'
                    AND knowledge_to IS NULL
                    AND CAST(observation_start AS DATE)
                        BETWEEN DATE '2020-01-01' AND DATE '2021-12-31'
                  ORDER BY listing_id, observation_start
                  LIMIT {limit}
                ), features AS (
                  SELECT
                    listing_id,
                    observation_start,
                    close / LAG(close) OVER (
                      PARTITION BY listing_id ORDER BY observation_start
                    ) - 1 AS return_1,
                    AVG(volume) OVER (
                      PARTITION BY listing_id ORDER BY observation_start
                      ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                    ) AS volume_mean20
                  FROM sampled
                )
                SELECT
                  COUNT(*) AS rows_seen,
                  SUM(CASE WHEN return_1 IS NULL THEN 0 ELSE 1 END) AS return_1_ready,
                  SUM(CASE WHEN volume_mean20 IS NULL THEN 0 ELSE 1 END) AS volume_mean20_ready
                FROM features
                """

                first_start = time.perf_counter()
                first_row = connection.execute(query).fetchone()
                first_seconds = time.perf_counter() - first_start
                if first_row is None:
                    raise StageTrainingCapacityError("real DWD benchmark returned no row")
                first = tuple(int(value) for value in first_row)

                second_start = time.perf_counter()
                second_row = connection.execute(query).fetchone()
                second_seconds = time.perf_counter() - second_start
                if second_row is None:
                    raise StageTrainingCapacityError("warm real DWD benchmark returned no row")
                second = tuple(int(value) for value in second_row)
                if first != second:
                    raise StageTrainingCapacityError(
                        "warm process probe differs from first process pass"
                    )
                results.append(
                    {
                        "row_limit": limit,
                        "rows_seen": first[0],
                        "derived_return_ready_rows": first[1],
                        "derived_volume_window_ready_rows": first[2],
                        "first_process_pass_seconds": first_seconds,
                        "second_process_pass_seconds": second_seconds,
                        "os_cache_state_claim": (
                            "unknown; first process pass is not claimed as cold disk"
                        ),
                        "peak_rss_bytes_after_stage": _peak_rss_bytes(),
                    }
                )
        finally:
            connection.close()
    return results


def _git_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_root()), "rev-parse", "HEAD"], text=True, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def run_capacity_benchmark(
    config: Mapping[str, Any], *, training_root: Path | None = None
) -> dict[str, Any]:
    preflight = verify_training_node(config, training_root=training_root)
    root = Path(preflight["training_root"])
    source_root = Path(preflight["source_root"])
    started = datetime.now(timezone.utc)
    synthetic = _synthetic_benchmark(config)
    real_dwd = _real_dwd_benchmark(config, source_root=source_root, training_root=root)
    finished = datetime.now(timezone.utc)
    post_resources = _resource_snapshot(root)
    report = {
        "schema_version": "quanthus.stage-training-capacity-report.v1",
        "work_item": "P3-STAGE-001-B",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": (finished - started).total_seconds(),
        "code_revision": _git_revision(),
        "environment": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "preflight": preflight,
        "synthetic": synthetic,
        "real_dwd": real_dwd,
        "post_resources": post_resources,
        "claim_limits": [
            "capacity benchmark only; no stock-stage classifier was trained",
            "real DWD reads are restricted to 2020-2021 development dates",
            "no market return, future target, label score or strategy result is emitted",
            "first process pass is not claimed as a cold operating-system cache run",
        ],
    }
    output_dir = root / str(_required_mapping(config, "benchmark")["report_relative_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = "stage-training-capacity-" + started.strftime("%Y%m%dT%H%M%SZ") + ".json"
    output = output_dir / filename
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["report_path"] = str(output)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("preflight", "benchmark"), help="validate or execute capacity probes"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_root() / "config/stage-learning/training-node.v1.json",
    )
    parser.add_argument("--training-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_capacity_config(args.config)
    if args.command == "preflight":
        result = verify_training_node(config, training_root=args.training_root)
    else:
        result = run_capacity_benchmark(config, training_root=args.training_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
