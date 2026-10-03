"""Private P3 Daily basis adaptation; every output stays unavailable for decisions."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value
from quanthus.expert_strategy.p3_daily_adapter_probe import (
    ProbeBudgetExceeded,
    _path,
    _timeout,
    _write,
    sha256,
    verify_packages,
)

CONFIG = "config/research/p3-strat-003/temporary-daily-adapter.v1.json"
CONFIG_SHA = "sha256:34bb16259845657112811286620b202dcce093a7009d859733735a6237976805"
IMPLEMENTATION = "src/quanthus/expert_strategy/p3_temporary_daily_adapter.py"
OUTPUT_SCHEMA = "quanthus.p3-temporary-daily-bars.v1"


def request(repo: Path, node: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if sha256(repo / CONFIG) != CONFIG_SHA:
        raise ValueError("TEMPORARY_ADAPTER_CONFIG_DRIFT")
    raw = json.loads((repo / CONFIG).read_bytes())
    sources = {}
    for key in ("source_request", "source_access_recovery"):
        bound = raw[key]
        path = _path(repo, bound["path"])
        if sha256(path) != bound["sha256"]:
            raise ValueError("TEMPORARY_ADAPTER_SOURCE_REQUEST_DRIFT")
        sources[key] = json.loads(path.read_bytes())
    research = sources["source_request"]["authority"]["original_research_request"]
    if sha256(_path(repo, research["path"])) != research["sha256"]:
        raise ValueError("TEMPORARY_ADAPTER_RESEARCH_AUTHORITY_DRIFT")
    packages = sources["source_request"]["packages"]
    if (
        len(packages) != raw["scope"]["packages"]
        or len({p["market_date"] for p in packages}) != len(packages)
        or sum(len(p["files"]) for p in packages) != raw["scope"]["payload_files"]
        or sum(f["size_bytes"] for p in packages for f in p["files"])
        != raw["scope"]["payload_bytes"]
        or any(
            not raw["scope"]["start"] <= p["market_date"] <= raw["scope"]["end"] for p in packages
        )
    ):
        raise ValueError("TEMPORARY_ADAPTER_SCOPE_DRIFT")
    relocation = sources["source_access_recovery"]["root_relocation"]
    matches = [p for p in packages if p["market_date"] == relocation["market_date"]]
    if len(matches) != 1 or matches[0]["root"] != relocation["old_root"]:
        raise ValueError("TEMPORARY_ADAPTER_RELOCATION_DRIFT")
    matches[0]["root"] = relocation["new_root"]
    selected = [p for p in packages if p["node"] == node]
    settings = raw["nodes"][node]
    declared_receipt = Path(settings["diagnostic_receipt"])
    receipt_path = _path(declared_receipt.parent, declared_receipt.name)
    if sha256(receipt_path) != settings["diagnostic_receipt_sha256"]:
        raise ValueError("TEMPORARY_ADAPTER_DIAGNOSTIC_DRIFT")
    receipt = json.loads(receipt_path.read_bytes())
    recorded = receipt.pop("result_digest")
    if (
        digest_value(receipt) != recorded
        or recorded != settings["diagnostic_result_digest"]
        or receipt["node"] != node
        or receipt["disposition"] != "INPUT_ONLY_ADAPTER_EVIDENCE"
        or {r["market_date"] for r in receipt["daily"]} != {p["market_date"] for p in selected}
        or len(receipt["daily"]) != len(selected)
        or any(r["raw_1d_rows"] != raw["scope"]["rows_per_package"] for r in receipt["daily"])
        or receipt["aggregate"]["unknown_basis_rows"] != 0
        or receipt["aggregate"]["invalid_price_rows"] != 0
        or receipt["B_accepted"] is not False
        or receipt["D_activated"] is not False
        or receipt["P6_or_formal_data_mutated"] is not False
    ):
        raise ValueError("TEMPORARY_ADAPTER_DIAGNOSTIC_UNQUALIFIED")
    return raw, selected


def private_root(raw: dict[str, Any], node: str) -> Path:
    settings = raw["nodes"][node]
    root = Path(settings["output_root"])
    if os.getuid() != settings["uid"] or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("TEMPORARY_ADAPTER_PRIVATE_ROOT_REQUIRED")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != settings["uid"] or root.stat().st_mode & 0o077:
        raise ValueError("TEMPORARY_ADAPTER_PRIVATE_ROOT_REQUIRED")
    return root


def prepare_code_receipt(repo: Path, node: str, revision: str) -> dict[str, Any]:
    raw, _ = request(repo, node)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("TEMPORARY_ADAPTER_EXACT_REVISION_REQUIRED")
    git = ["git", "-c", f"safe.directory={repo}", "-C", str(repo)]
    if subprocess.check_output([*git, "rev-parse", "HEAD"]).decode().strip() != revision or (
        subprocess.check_output([*git, "status", "--porcelain"])
    ):
        raise ValueError("TEMPORARY_ADAPTER_CLEAN_CHECKOUT_REQUIRED")
    paths = subprocess.check_output([*git, "ls-files", "-z", "src/quanthus", "config"])
    files = {p: sha256(_path(repo, p)) for p in paths.decode().split("\0") if p}
    _write(
        private_root(raw, node) / "code-receipt.json", {"code_revision": revision, "files": files}
    )
    return {"code_revision": revision, "files": len(files)}


def adapt_package(
    c: Any, package: dict[str, Any], output: Path, raw: dict[str, Any]
) -> dict[str, Any]:
    paths = {
        "bar" if "/dwd_security_bar/" in f["path"] else "calendar": str(
            Path(package["root"]) / f["path"]
        )
        for f in package["files"]
    }
    if len(paths) != 2 or len(package["files"]) != 2:
        raise ValueError("TEMPORARY_ADAPTER_SOURCE_CLOSURE_INVALID")
    c.execute(
        "CREATE TEMP TABLE adapter_calendar AS SELECT * FROM read_parquet(?)", [paths["calendar"]]
    )
    if c.execute(
        "SELECT count(*) FROM (SELECT calendar_session_id FROM adapter_calendar "
        "GROUP BY 1 HAVING count(*)<>1)"
    ).fetchone()[0]:
        raise ValueError("TEMPORARY_ADAPTER_CALENDAR_DUPLICATE")
    source_columns = [
        r[0] for r in c.execute("DESCRIBE SELECT * FROM read_parquet(?)", [paths["bar"]]).fetchall()
    ]
    added = {
        "source_price_basis",
        "adapter_policy_version",
        "decision_admissible",
        "unavailable_reason",
        "probe_date",
        "probe_close",
        "probe_open",
        "probe_calendar_missing",
    }
    if added.intersection(source_columns):
        raise ValueError("TEMPORARY_ADAPTER_ALREADY_ADAPTED_SOURCE")
    c.execute(
        """CREATE TEMP TABLE adapter_daily AS SELECT b.*,
        c.trade_date AS probe_date, c.close_at AS probe_close, c.is_open AS probe_open,
        c.calendar_session_id IS NULL AS probe_calendar_missing
        FROM read_parquet(?) b LEFT JOIN adapter_calendar c USING(calendar_session_id)
        WHERE b.interval_size='1d'""",
        [paths["bar"]],
    )
    if c.execute(
        """SELECT count(*) FROM adapter_daily WHERE probe_calendar_missing
        OR probe_date IS DISTINCT FROM ?::DATE OR NOT coalesce(probe_open,false)
        OR listing_id IS NULL OR knowledge_from IS NULL OR probe_close IS NULL
        OR price_basis IS DISTINCT FROM 'none'""",
        [package["market_date"]],
    ).fetchone()[0]:
        raise ValueError("TEMPORARY_ADAPTER_IDENTITY_BASIS_OR_TIME_INVALID")
    if c.execute(
        "SELECT count(*) FROM (SELECT listing_id,calendar_session_id FROM adapter_daily "
        "GROUP BY ALL HAVING count(*)<>1)"
    ).fetchone()[0]:
        raise ValueError("TEMPORARY_ADAPTER_IDENTITY_DUPLICATE")
    invalid = " OR ".join(
        f'"{v}" IS NULL OR NOT isfinite("{v}")'
        for v in ("open", "high", "low", "close", "volume", "turnover")
    )
    invalid += ' OR "open"<=0 OR "high"<=0 OR "low"<=0 OR "close"<=0 OR volume<0 OR turnover<0'
    invalid += ' OR "high"<greatest("open","low","close") OR "low">least("open","high","close")'
    if c.execute(f"SELECT count(*) FROM adapter_daily WHERE {invalid}").fetchone()[0]:
        raise ValueError("TEMPORARY_ADAPTER_PRICE_INVALID")
    n, late, zero = c.execute(
        "SELECT count(*),count(*) FILTER(WHERE knowledge_from>probe_close),"
        "count(*) FILTER(WHERE volume=0 OR turnover=0) FROM adapter_daily"
    ).fetchone()
    if n != raw["scope"]["rows_per_package"]:
        raise ValueError("TEMPORARY_ADAPTER_DENOMINATOR_DIFFERS")
    # Preserve all source columns and types; rename only the original basis and add fixed metadata.
    columns = ",".join(
        '"' + v.replace('"', '""') + '"' for v in source_columns if v != "price_basis"
    )
    contract = raw["contract"]
    query = f"""SELECT {columns},price_basis AS source_price_basis,
        ?::VARCHAR AS price_basis,?::VARCHAR AS adapter_policy_version,
        false AS decision_admissible,?::VARCHAR AS unavailable_reason
        FROM adapter_daily ORDER BY listing_id,calendar_session_id"""
    # DuckDB assigns a positional COPY TO parameter before the SELECT parameters.
    # Quote the fixed private path as SQL, so binding order cannot redirect publication.
    quoted_output = "'" + str(output).replace("'", "''") + "'"
    c.execute(
        f"COPY ({query}) TO {quoted_output} (FORMAT PARQUET, COMPRESSION ZSTD)",
        [
            contract["canonical_price_basis"],
            raw["policy_version"],
            contract["unavailable_reason"],
        ],
    )
    # Compare every raw column after serialization, including exact Decimal/time/provenance.
    original = ",".join('"' + v.replace('"', '""') + '"' for v in source_columns)
    restored = ",".join(
        "source_price_basis AS price_basis"
        if v == "price_basis"
        else '"' + v.replace('"', '""') + '"'
        for v in source_columns
    )
    if c.execute(
        f"""SELECT count(*) FROM (
        (SELECT {original} FROM adapter_daily EXCEPT ALL SELECT {restored} FROM read_parquet(?))
        UNION ALL (SELECT {restored} FROM read_parquet(?)
        EXCEPT ALL SELECT {original} FROM adapter_daily))""",
        [str(output), str(output)],
    ).fetchone()[0]:
        raise ValueError("TEMPORARY_ADAPTER_SERIALIZED_SOURCE_DRIFT")
    if c.execute(
        "SELECT count(*) FROM read_parquet(?) WHERE price_basis<>? "
        "OR decision_admissible IS DISTINCT FROM false OR unavailable_reason<>?",
        [str(output), contract["canonical_price_basis"], contract["unavailable_reason"]],
    ).fetchone()[0]:
        raise ValueError("TEMPORARY_ADAPTER_OUTPUT_CONTRACT_DRIFT")
    c.execute("DROP TABLE adapter_daily")
    c.execute("DROP TABLE adapter_calendar")
    return {
        "market_date": package["market_date"],
        "rows": n,
        "raw_known_after_close_rows": late,
        "zero_activity_rows": zero,
        "source_column_count": len(source_columns),
        "source_differences": 0,
        "path": output.name,
        "sha256": sha256(output),
        "size_bytes": output.stat().st_size,
    }


def build(repo: Path, node: str, revision: str) -> dict[str, Any]:
    raw, packages = request(repo, node)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("TEMPORARY_ADAPTER_EXACT_REVISION_REQUIRED")
    root = private_root(raw, node)
    code_path = _path(root, "code-receipt.json")
    code = json.loads(code_path.read_bytes())
    if code["code_revision"] != revision or IMPLEMENTATION not in code["files"]:
        raise ValueError("TEMPORARY_ADAPTER_CODE_RECEIPT_DRIFT")
    for relative, expected in code["files"].items():
        if sha256(_path(repo, relative)) != expected:
            raise ValueError("TEMPORARY_ADAPTER_CODE_BYTES_DRIFT")
    if sha256(Path(__file__)) != code["files"][IMPLEMENTATION]:
        raise ValueError("TEMPORARY_ADAPTER_LOADED_CODE_DRIFT")
    identity = {
        "config_sha256": CONFIG_SHA,
        "code_revision": revision,
        "code_receipt_sha256": sha256(code_path),
        "source_packages": packages,
        "node": node,
        "output_schema": OUTPUT_SCHEMA,
        "scope": raw["scope"],
    }
    cache_digest = digest_value(identity)
    _write(root / "attempt.json", {"identity": cache_digest, "maximum_attempts": 1})
    output = root / cache_digest.removeprefix("sha256:")
    output.mkdir(mode=0o700)
    started = time.monotonic()
    handler = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(raw["budgets"]["wall_seconds_per_node"])
    try:
        source_bytes = verify_packages(packages)
        verified = time.monotonic()
        import duckdb

        with tempfile.TemporaryDirectory(prefix="adapter-tmp-", dir=output) as temp:
            c = duckdb.connect(":memory:")
            try:
                for k, v in (
                    ("threads", raw["budgets"]["threads"]),
                    ("memory_limit", raw["budgets"]["memory_limit"]),
                    ("max_temp_directory_size", raw["budgets"]["tmp_limit"]),
                    ("temp_directory", temp),
                ):
                    c.execute(f"SET {k}=?", [v])
                daily = []
                published_bytes = 0
                for package in packages:
                    daily.append(
                        adapt_package(
                            c, package, output / (package["market_date"] + ".parquet"), raw
                        )
                    )
                    published_bytes += daily[-1]["size_bytes"]
                    if published_bytes > raw["budgets"]["output_max_bytes_per_node"]:
                        raise ProbeBudgetExceeded("TEMPORARY_ADAPTER_OUTPUT_BUDGET_EXCEEDED")
                tmp_bytes = sum(p.stat().st_size for p in Path(temp).rglob("*") if p.is_file())
            finally:
                c.close()
        result = {
            "schema_version": "quanthus.p3-temporary-daily-adapter-receipt.v1",
            "disposition": "TEMPORARY_BASIS_ADAPTED_PIT_UNQUALIFIED",
            "identity": identity,
            "cache_digest": cache_digest,
            "output_root": str(output),
            "daily": daily,
            "rows": sum(d["rows"] for d in daily),
            "source_differences": 0,
            "decision_admissible_rows": 0,
            "raw_times_changed": False,
            "B_accepted": False,
            "D_activated": False,
            "P6_or_formal_data_mutated": False,
            "candidate_scan_or_economics_or_labels": False,
            "data_issues_closed": [],
            "lifecycle": raw["lifecycle"],
            "stages": [
                {
                    "stage": "verify_complete_source_bytes",
                    "bytes": source_bytes,
                    "files": len(packages) * 2,
                    "elapsed_seconds": round(verified - started, 6),
                },
                {
                    "stage": "adapt_and_compare_serialized_raw_values",
                    "disposition": "one_tmp_per_package_reused_for_validation_copy_and_comparison",
                    "rows": sum(d["rows"] for d in daily),
                    "bytes": sum(d["size_bytes"] for d in daily),
                    "tmp_bytes_at_close": tmp_bytes,
                    "tmp_removed": True,
                    "elapsed_seconds": round(time.monotonic() - verified, 6),
                },
            ],
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        result["result_digest"] = digest_value(result)
        if (
            published_bytes + len(canonical_json_bytes(result)) + 1
            > raw["budgets"]["output_max_bytes_per_node"]
        ):
            raise ProbeBudgetExceeded("TEMPORARY_ADAPTER_OUTPUT_BUDGET_EXCEEDED")
        _write(output / "receipt.json", result)
        return result
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, handler)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repository-root", type=Path, required=True)
    p.add_argument("--node", choices=("OCI", "CN01"), required=True)
    p.add_argument("--code-revision", required=True)
    p.add_argument("--prepare-code-receipt", action="store_true")
    a = p.parse_args()
    result = (prepare_code_receipt if a.prepare_code_receipt else build)(
        a.repository_root, a.node, a.code_revision
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
