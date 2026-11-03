"""Isolated successor Daily: existing CN01 core plus source-observed native 5m.

No collection, legacy pointer publication, or production activation is performed.
The old core remains byte-identical evidence; consumers read the canonical table
paths in the successor manifest. Original clock evidence stays in source members.
"""

from __future__ import annotations

import importlib
import json
import shutil
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import canonical, file_sha
from .base22_sources import _tree, disjoint_output, physical_path
from .base24_image import KIND as BASE_KIND
from .closing_auction_source import PROVIDER as AUCTION_PROVIDER
from .daily_core_domain import _TABLE_KEYS, verify_daily_domain_increment
from .native5m_daily_source import verify_native5m_source
from .native_base24_reader import require_daily_core_governance

KIND = "cn02_base24_daily_1d_native5m_v1"
SCOPE = "p10-base24-1d5m-v1"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("BASE24_DAILY_JSON_INVALID")
    return value


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def mapping(core: Path, day: str) -> tuple[dict[str, dict[str, Any]], Any]:
    import re

    pq = importlib.import_module("pyarrow.parquet")
    out = {}
    schema = None
    for path in sorted(core.glob("payload/*/dwd_security_bar/market_date=*/*.parquet")):
        pf = pq.ParquetFile(path)
        schema = pf.schema_arrow
        for row in pf.read().to_pylist():
            match = re.fullmatch(
                r"[^#]+#code=([0-9]{6})&date=(\d{4}-\d{2}-\d{2})", row["source_record_id"]
            )
            if (
                not match
                or match[2] != day
                or row["interval_size"] != "1d"
                or row["price_basis"] != "none"
                or match[1] in out
            ):
                raise ValueError("BASE24_DAILY_1D_MAPPING_INVALID")
            out[match[1]] = row
    states = set()
    for path in sorted(core.glob("payload/*/dwd_tradability_state/market_date=*/*.parquet")):
        for row in pq.ParquetFile(path).read().to_pylist():
            match = re.fullmatch(
                r"[^#]+#code=([0-9]{6})&date=(\d{4}-\d{2}-\d{2})", row["source_record_id"]
            )
            if not match or match[2] != day or match[1] in states:
                raise ValueError("BASE24_DAILY_STATE_MAPPING_INVALID")
            code = match[1]
            states.add(code)
            if code in out:
                if any(
                    row[k] != out[code][k]
                    for k in ["listing_id", "calendar_session_id", "observation_start"]
                ):
                    raise ValueError("BASE24_DAILY_CALENDAR_OR_LISTING_CONFLICT")
                out[code]["is_tradable"] = row["is_tradable"]
            else:
                if any(row[k] is not False for k in ["is_tradable", "can_buy", "can_sell"]):
                    raise ValueError("BASE24_DAILY_MISSING_BAR_NOT_JUSTIFIED")
                out[code] = {
                    **row,
                    "close": None,
                    "volume": None,
                    "daily_price_observation_state": "unavailable_suspended",
                }
    manifest = _json(core / "manifest.json")
    universe = manifest.get("acquisition_universe", {})
    if (
        schema is None
        or not states
        or set(out) != states
        or set(out) != set(universe.get("codes", []))
    ):
        raise ValueError("BASE24_DAILY_EXACT_UNIVERSE_REQUIRED")
    return out, schema


def _rows(native: Path, daily: Mapping[str, Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    pq = importlib.import_module("pyarrow.parquet")
    statuses = {
        x["stock_code"]: x
        for x in (json.loads(s) for s in (native / "quality-status.jsonl").read_text().splitlines())
    }
    manifest = _json(native / "manifest.json")
    sealed = datetime.fromisoformat(manifest["sealed_at"])
    manifest_suffix = file_sha(native / "manifest.json")[7:19]
    for batch in pq.ParquetFile(native / "minute-real-observed.parquet").iter_batches(
        batch_size=8192, use_threads=False
    ):
        for row in batch.to_pylist():
            code = row["stock_code"]
            parent = daily[code]
            observed_bound = datetime.fromisoformat(statuses[code]["source_acquired_at_or_after"])
            end = (
                datetime.fromisoformat(row["source_ts"])
                .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                .astimezone(timezone.utc)
            )
            source_name = (
                AUCTION_PROVIDER
                if statuses[code].get("source_composition") == AUCTION_PROVIDER
                else "baostock-native-5m"
            )
            yield {
                "record_source": source_name,
                "source_record_id": f"{code}.json.gz#code={code}&ts={row['source_ts']}",
                "source_revision": source_name + "@" + manifest_suffix,
                "source_artifact_sha256": row["raw_sha256"],
                "knowledge_from": max(sealed, observed_bound, parent["knowledge_from"]),
                "knowledge_to": None,
                "observation_start": end - timedelta(minutes=5),
                "observation_end": end,
                "listing_id": parent["listing_id"],
                "calendar_session_id": parent["calendar_session_id"],
                "interval_size": "5m",
                "price_basis": "UNADJUSTED",
                "currency": parent["currency"],
                **{k: row[k] for k in ["open", "high", "low", "close"]},
                "volume": row["volume_shares"],
                "turnover": row["amount"],
                "trade_count": None,
            }


def _inputs(
    core: Path, native: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    require_daily_core_governance(config)
    verify_daily_domain_increment(core)
    manifest = _json(core / "manifest.json")
    base = manifest.get("base", {})
    expected = config["layers"]["dwd"]
    if (
        base.get("kind") != BASE_KIND
        or base.get("business_through") != config.get("through_date")
        or any(
            base.get(k) != expected[k]
            for k in ["manifest_sha256", "content_digest", "materialization_sha256", "package_id"]
        )
    ):
        raise ValueError("BASE24_DAILY_BASE_BINDING_DIFFERS")
    day = manifest["market_date"]
    daily, schema = mapping(core, day)
    native_sha = file_sha(native / "manifest.json")
    proof = verify_native5m_source(
        native,
        native_sha,
        day=day,
        daily=daily,
        daily_sources={k: v["manifest_sha256"] for k, v in manifest["selected_releases"].items()},
    )
    return manifest, daily, schema, proof


def _identity(core: Path, native: Path, config: Path, code_revision: str) -> dict[str, Any]:
    if len(code_revision) != 40 or any(x not in "0123456789abcdef" for x in code_revision):
        raise ValueError("BASE24_DAILY_CODE_REVISION_INVALID")
    return {
        "core": file_sha(core / "manifest.json"),
        "native5m": file_sha(native / "manifest.json"),
        "config": file_sha(config),
        "code": code_revision,
        "scope": SCOPE,
    }


def _table_paths(root: Path) -> dict[str, list[str]]:
    paths = {"dwd_security_bar": ["payload/1d.parquet", "payload/5m.parquet"]}
    for table in sorted(set(_TABLE_KEYS) - {"dwd_security_bar"}):
        files = sorted(root.glob(f"members/core/payload/*/{table}/market_date=*/*.parquet"))
        if not files:
            raise ValueError("BASE24_DAILY_CANONICAL_TABLE_PATHS_INVALID")
        paths[table] = [p.relative_to(root).as_posix() for p in files]
    return paths


def read_tables(root: Path) -> dict[str, tuple[Path, ...]]:
    """Qualify once per Job, then share all seven immutable local table paths.

    Archived core bars are provenance only. Only the two canonical bar files
    enter the reader; no old price-basis alias or one-minute prerequisite leaks in.
    """
    result = verify(root)
    return {table: tuple(root / p for p in paths) for table, paths in result["table_paths"].items()}


def build(
    *, core: Path, native5m: Path, config: Path, output: Path, code_revision: str
) -> dict[str, Any]:
    sources = [core, native5m, config]
    output = disjoint_output(output, sources)
    stage = disjoint_output(output.with_name("." + output.name + ".staging"), sources)
    core, native5m, config = (physical_path(p) for p in sources)
    identity = _identity(core, native5m, config, code_revision)
    if output.exists():
        result = verify(output)
        if result["identity"] != identity:
            raise ValueError("BASE24_DAILY_EXISTING_JOB_IDENTITY_DIFFERS")
        return result
    manifest, daily, schema, proof = _inputs(core, native5m, _json(config))
    if stage.exists():
        raise ValueError("BASE24_DAILY_STAGING_EXISTS_PRESERVED")
    stage.mkdir(mode=0o700)
    pa, pq = importlib.import_module("pyarrow"), importlib.import_module("pyarrow.parquet")
    try:
        shutil.copytree(core, stage / "members/core")
        shutil.copytree(native5m, stage / "members/native5m")
        shutil.copyfile(config, stage / "config.json")
        payload = stage / "payload"
        payload.mkdir()
        daily_rows = [
            {k: row[k] for k in schema.names}
            for row in daily.values()
            if "daily_price_observation_state" not in row
        ]
        original = pa.Table.from_pylist(daily_rows, schema=schema)
        index = schema.get_field_index("price_basis")
        mapped = original.set_column(
            index, schema.field(index), pa.array(["UNADJUSTED"] * len(original), type=pa.string())
        )
        pq.write_table(mapped, payload / "1d.parquet", compression="zstd")
        restored = (
            pq.ParquetFile(payload / "1d.parquet")
            .read()
            .set_column(index, schema.field(index), original.column(index))
        )
        if not original.equals(restored):
            raise ValueError("BASE24_DAILY_1D_VALUES_OR_CLOCK_CHANGED")
        rows = 0
        with pq.ParquetWriter(payload / "5m.parquet", schema, compression="zstd") as writer:
            buffer = []
            for row in _rows(stage / "members/native5m", daily):
                buffer.append(row)
                if len(buffer) == 8192:
                    writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                    rows += len(buffer)
                    buffer.clear()
            if buffer:
                writer.write_table(pa.Table.from_pylist(buffer, schema=schema))
                rows += len(buffer)
        if rows != proof["rows"]:
            raise ValueError("BASE24_DAILY_5M_ROWS_CHANGED")
        # All copied input members remain independent entity files, never hardlinks.
        inventory = [
            {
                "path": rel,
                "size_bytes": (stage / rel).stat().st_size,
                "sha256": file_sha(stage / rel),
            }
            for rel in sorted(_tree(stage))
        ]
        document = {
            "schema_version": 1,
            "kind": KIND,
            "product_scope": SCOPE,
            "identity": identity,
            "base": manifest["base"],
            "market_date": manifest["market_date"],
            "default_intervals": ["1d", "5m"],
            "one_minute": "NOT_IN_PRODUCT",
            "clock_policy": (
                "conservative_warehouse_known_by_source_seal_and_daily_dependency; "
                "provider acquisition remains unknown"
            ),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_proof": proof,
            "row_counts": {"1d": len(original), "5m": rows},
            "table_paths": _table_paths(stage),
            "consumer_admission": "NOT_GRANTED",
            "execution_authorized": False,
            "files": inventory,
        }
        _write(stage / "manifest.json", document)
        (stage / "READY").write_text(file_sha(stage / "manifest.json") + "\n")
        # Complete verification precedes the immutable directory publication.
        result = _verify(stage, trusted_inputs=(manifest, daily, schema, proof))
        if output.exists():
            raise ValueError("BASE24_DAILY_OUTPUT_APPEARED")
        stage.rename(output)
        return result
    except BaseException as exc:
        (stage / "READY").unlink(missing_ok=True)
        _write(
            stage / "FAILURE.safe.json",
            {
                "status": "FAILED_NOT_READY",
                "exception_type": type(exc).__name__,
                "identity": identity,
            },
        )
        raise


def verify(root: Path) -> dict[str, Any]:
    """Independent mode always replays the original native source closure."""
    return _verify(root)


def _verify(
    root: Path,
    *,
    trusted_inputs: tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = physical_path(root)
    manifest = _json(root / "manifest.json")
    if (
        manifest.get("kind") != KIND
        or manifest.get("product_scope") != SCOPE
        or manifest.get("default_intervals") != ["1d", "5m"]
        or manifest.get("one_minute") != "NOT_IN_PRODUCT"
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or manifest.get("execution_authorized") is not False
        or (root / "READY").read_text().strip() != file_sha(root / "manifest.json")
    ):
        raise ValueError("BASE24_DAILY_SCOPE_OR_SEAL_INVALID")
    declared = set()
    physical_hashes = {}
    for entry in manifest["files"]:
        relative = entry["path"]
        part = Path(relative)
        if (
            part.is_absolute()
            or ".." in part.parts
            or part.as_posix() != relative
            or relative in declared
        ):
            raise ValueError("BASE24_DAILY_FILE_PATH_INVALID")
        p = physical_path(root / relative)
        if (
            not p.is_file()
            or p.stat().st_nlink != 1
            or p.stat().st_size != entry["size_bytes"]
            or file_sha(p) != entry["sha256"]
        ):
            raise ValueError("BASE24_DAILY_FILE_CHANGED")
        declared.add(relative)
        physical_hashes[relative] = entry["sha256"]
    if _tree(root) != declared | {"manifest.json", "READY"}:
        raise ValueError("BASE24_DAILY_PHYSICAL_INVENTORY_INVALID")
    table_paths = _table_paths(root)
    if manifest.get("table_paths") != table_paths or any(
        path not in declared for paths in table_paths.values() for path in paths
    ):
        raise ValueError("BASE24_DAILY_CANONICAL_TABLE_PATHS_INVALID")
    cfg = root / "config.json"
    core, native = root / "members/core", root / "members/native5m"
    if _identity(core, native, cfg, manifest["identity"]["code"]) != manifest["identity"]:
        raise ValueError("BASE24_DAILY_JOB_IDENTITY_INVALID")
    if trusted_inputs is None:
        child, daily, schema, proof = _inputs(core, native, _json(cfg))
    else:
        # Build mode reuses its one qualified input descriptor only after exact
        # copied member/control hashes prove the entire closure unchanged.
        # Public cold verification never takes this path.
        for member, size_key in [(core, "size_bytes"), (native, "bytes")]:
            prefix = member.relative_to(root).as_posix() + "/"
            source_manifest = _json(member / "manifest.json")
            source_entries = source_manifest["files"]
            expected_paths = {prefix + e["path"] for e in source_entries}
            expected_paths |= {prefix + "manifest.json", prefix + "READY"}
            if expected_paths != {p for p in declared if p.startswith(prefix)}:
                raise ValueError("BASE24_DAILY_COPIED_SOURCE_CLOSURE_DIFFERS")
            for entry in source_entries:
                path = member / entry["path"]
                if (
                    physical_hashes[prefix + entry["path"]] != entry["sha256"]
                    or path.stat().st_size != entry[size_key]
                ):
                    raise ValueError("BASE24_DAILY_COPIED_SOURCE_CLOSURE_DIFFERS")
            if (member / "READY").read_text().strip() != file_sha(member / "manifest.json"):
                raise ValueError("BASE24_DAILY_COPIED_SOURCE_CLOSURE_DIFFERS")
        child, daily, schema, proof = trusted_inputs
    if (
        manifest["base"] != child["base"]
        or manifest["market_date"] != child["market_date"]
        or manifest["source_proof"] != proof
    ):
        raise ValueError("BASE24_DAILY_CHILD_DEPENDENCY_DIFFERS")
    pa, pq = importlib.import_module("pyarrow"), importlib.import_module("pyarrow.parquet")
    observed = pq.ParquetFile(root / "payload/1d.parquet").read()
    original = pa.Table.from_pylist(
        [
            {k: row[k] for k in schema.names}
            for row in daily.values()
            if "daily_price_observation_state" not in row
        ],
        schema=schema,
    )
    index = schema.get_field_index("price_basis")
    expected = original.set_column(
        index, schema.field(index), pa.array(["UNADJUSTED"] * len(original))
    )
    if not observed.equals(expected):
        raise ValueError("BASE24_DAILY_CANONICAL_1D_DIFFERS")
    iterator = iter(_rows(native, daily))
    rows = 0
    for batch in pq.ParquetFile(root / "payload/5m.parquet").iter_batches(
        batch_size=8192, use_threads=False
    ):
        expected_rows = [next(iterator) for _ in range(batch.num_rows)]
        if not batch.equals(pa.RecordBatch.from_pylist(expected_rows, schema=schema)):
            raise ValueError("BASE24_DAILY_CANONICAL_5M_DIFFERS")
        rows += batch.num_rows
    if (
        next(iterator, None) is not None
        or manifest["row_counts"] != {"1d": len(original), "5m": rows}
        or rows != proof["rows"]
    ):
        raise ValueError("BASE24_DAILY_CANONICAL_CENSUS_DIFFERS")
    return {
        "status": "PASS_ISOLATED_1D_NATIVE5M_NOT_NATURAL_T_DAY_OR_WEEKLY",
        "manifest_sha256": file_sha(root / "manifest.json"),
        "identity": manifest["identity"],
        "market_date": manifest["market_date"],
        "row_counts": manifest["row_counts"],
        "table_paths": table_paths,
        "source_grades": proof["grades"],
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
    }
