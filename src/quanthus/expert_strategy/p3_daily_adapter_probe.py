"""Frozen, aggregate-only Daily adapter probe. Never qualifies B or projects PIT times."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import canonical_json_bytes, digest_value

REQUEST_CONFIG = "config/research/p3-strat-003/daily-adapter-probe.v1.json"
REQUEST_SHA = "sha256:396b823a16ee28b2636b90a8b915f26ab6897cf2e0edd6b54ed5186a2ce78f3c"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def _path(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise ValueError("ADAPTER_SOURCE_PATH_INVALID")
    return path


def verify_packages(packages: Sequence[dict[str, Any]]) -> int:
    """Verify the complete selected-node closure before opening any Parquet file."""
    total = 0
    for package in packages:
        root = Path(package["root"])
        manifest = _path(root, "manifest.json")
        if sha256(manifest) != package["manifest_sha256"]:
            raise ValueError("ADAPTER_MANIFEST_DRIFT")
        if _path(root, "READY").read_text().strip() != package["manifest_sha256"]:
            raise ValueError("ADAPTER_READY_DRIFT")
        raw = json.loads(manifest.read_bytes())
        for name in ("market_date", "package_id", "code_revision"):
            if raw[name] != package[name]:
                raise ValueError("ADAPTER_PACKAGE_IDENTITY_DRIFT")
        declared = {f["path"]: f for f in raw["files"]}
        if len(declared) != len(raw["files"]):
            raise ValueError("ADAPTER_DUPLICATE_SOURCE_MEMBER")
        for member in package["files"]:
            if declared.get(member["path"]) != member:
                raise ValueError("ADAPTER_MEMBER_BINDING_DRIFT")
            path = _path(root, member["path"])
            if path.stat().st_size != member["size_bytes"] or sha256(path) != member["sha256"]:
                raise ValueError("ADAPTER_PAYLOAD_DRIFT")
            total += member["size_bytes"]
    return total


def probe_package(connection: Any, package: dict[str, Any]) -> dict[str, Any]:
    """Build the 1d TMP once, retaining raw values/times, then reuse for every count."""
    paths = {
        "bar" if "/dwd_security_bar/" in f["path"] else "calendar": str(
            Path(package["root"]) / f["path"]
        )
        for f in package["files"]
    }
    if len(paths) != 2 or len(package["files"]) != 2:
        raise ValueError("ADAPTER_SOURCE_CLOSURE_INVALID")
    connection.execute(
        "CREATE TEMP TABLE probe_calendar AS SELECT * FROM read_parquet(?)", [paths["calendar"]]
    )
    if connection.execute(
        "SELECT count(*) FROM (SELECT calendar_session_id FROM probe_calendar "
        "GROUP BY 1 HAVING count(*)<>1)"
    ).fetchone()[0]:
        raise ValueError("ADAPTER_CALENDAR_IDENTITY_DUPLICATE")
    connection.execute(
        """CREATE TEMP TABLE probe_1d AS SELECT b.*,c.trade_date,c.close_at,
        c.is_open,c.calendar_session_id IS NULL AS calendar_missing
        FROM read_parquet(?) b LEFT JOIN probe_calendar c USING(calendar_session_id)
        WHERE b.interval_size='1d'""",
        [paths["bar"]],
    )
    if connection.execute(
        """SELECT count(*) FROM probe_1d WHERE calendar_missing
        OR trade_date IS DISTINCT FROM CAST(? AS DATE) OR NOT coalesce(is_open,false)""",
        [package["market_date"]],
    ).fetchone()[0]:
        raise ValueError("ADAPTER_CALENDAR_SCOPE_INVALID")
    if connection.execute("SELECT count(*) FROM probe_1d WHERE listing_id IS NULL").fetchone()[0]:
        raise ValueError("ADAPTER_1D_IDENTITY_NULL")
    if connection.execute(
        "SELECT count(*) FROM (SELECT listing_id,calendar_session_id FROM probe_1d "
        "GROUP BY ALL HAVING count(*)<>1)"
    ).fetchone()[0]:
        raise ValueError("ADAPTER_1D_IDENTITY_DUPLICATE")
    values = ("open", "high", "low", "close", "volume", "turnover")
    invalid = " OR ".join(f'"{v}" IS NULL OR NOT isfinite("{v}")' for v in values)
    invalid += ' OR "open"<=0 OR "high"<=0 OR "low"<=0 OR "close"<=0 OR volume<0 OR turnover<0'
    invalid += ' OR "high"<greatest("open","low","close") OR "low">least("open","high","close")'
    query = f"""SELECT count(*) AS raw_1d_rows,
        count(*) FILTER(WHERE price_basis='UNADJUSTED') AS legacy_selected_rows,
        count(*) FILTER(WHERE price_basis='none') AS corrected_basis_rows,
        count(*) FILTER(WHERE price_basis IS DISTINCT FROM 'none') AS unknown_basis_rows,
        count(*) FILTER(WHERE {invalid}) AS invalid_price_rows,
        count(*) FILTER(WHERE volume=0 OR turnover=0) AS zero_activity_rows,
        count(*) FILTER(WHERE knowledge_from IS NULL OR close_at IS NULL) AS unknown_raw_time_rows,
        count(*) FILTER(WHERE knowledge_from>close_at) AS raw_known_after_close_rows,
        count(*) FILTER(WHERE knowledge_from<=close_at) AS raw_known_by_close_rows
        FROM probe_1d"""
    cursor = connection.execute(query)
    row = dict(zip((d[0] for d in cursor.description), cursor.fetchone(), strict=True))
    connection.execute("DROP TABLE probe_1d")
    connection.execute("DROP TABLE probe_calendar")
    return {"market_date": package["market_date"], **row}


def _write(path: Path, payload: dict[str, Any]) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ProbeBudgetExceeded(BaseException):
    """Stop this diagnostic without retrying its immutable attempt."""


def _timeout(_signal: int, _frame: Any) -> None:
    raise ProbeBudgetExceeded("ADAPTER_PROBE_WALL_BUDGET_EXCEEDED")


def prepare_code_receipt(*, repo: Path, node: str, code_revision: str) -> dict[str, Any]:
    """Bind the complete production/config tree on the clean Git host before row access."""
    if sha256(repo / REQUEST_CONFIG) != REQUEST_SHA:
        raise ValueError("ADAPTER_REQUEST_DRIFT")
    raw = json.loads((repo / REQUEST_CONFIG).read_bytes())
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("ADAPTER_EXACT_REVISION_REQUIRED")
    git = ["git", "-c", f"safe.directory={repo}", "-C", str(repo)]
    if subprocess.check_output(
        [*git, "rev-parse", "HEAD"]
    ).decode().strip() != code_revision or subprocess.check_output([*git, "status", "--porcelain"]):
        raise ValueError("ADAPTER_EXACT_CLEAN_CHECKOUT_REQUIRED")
    paths = subprocess.check_output([*git, "ls-files", "-z", "src/quanthus", "config"])
    files = {p: sha256(repo / p) for p in paths.decode().split("\0") if p}
    settings = raw["nodes"][node]
    output = Path(settings["output"])
    if os.getuid() != settings["uid"] or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("ADAPTER_PRIVATE_OUTPUT_REQUIRED")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.parent.stat().st_mode & 0o077 or output.parent.stat().st_uid != settings["uid"]:
        raise ValueError("ADAPTER_PRIVATE_OUTPUT_REQUIRED")
    _write(output.parent / "code-receipt.json", {"code_revision": code_revision, "files": files})
    return {"code_revision": code_revision, "file_count": len(files)}


def build(*, repo: Path, node: str, code_revision: str) -> dict[str, Any]:
    request_path = repo / REQUEST_CONFIG
    if sha256(request_path) != REQUEST_SHA:
        raise ValueError("ADAPTER_REQUEST_DRIFT")
    raw = json.loads(request_path.read_bytes())
    authority = raw["authority"]["original_research_request"]
    if sha256(repo / authority["path"]) != authority["sha256"]:
        raise ValueError("ADAPTER_RESEARCH_AUTHORITY_DRIFT")
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("ADAPTER_EXACT_REVISION_REQUIRED")
    settings = raw["nodes"][node]
    output = Path(settings["output"])
    if (
        os.getuid() != settings["uid"]
        or output.parent.stat().st_uid != settings["uid"]
        or output.parent.stat().st_mode & 0o077
        or any(p.is_symlink() for p in (output, *output.parents))
    ):
        raise ValueError("ADAPTER_PRIVATE_OUTPUT_REQUIRED")
    receipt_path = _path(output.parent, "code-receipt.json")
    receipt = json.loads(receipt_path.read_bytes())
    implementation = "src/quanthus/expert_strategy/p3_daily_adapter_probe.py"
    if receipt["code_revision"] != code_revision or implementation not in receipt["files"]:
        raise ValueError("ADAPTER_CODE_RECEIPT_DRIFT")
    for relative, expected in receipt["files"].items():
        if sha256(_path(repo, relative)) != expected:
            raise ValueError("ADAPTER_CODE_BYTES_DRIFT")
    if sha256(Path(__file__)) != receipt["files"][implementation]:
        raise ValueError("ADAPTER_LOADED_IMPLEMENTATION_DRIFT")
    marker = output.parent / "attempt.json"
    _write(
        marker,
        {
            "request_sha256": REQUEST_SHA,
            "code_revision": code_revision,
            "node": node,
            "maximum_attempts": 1,
        },
    )
    output.mkdir(mode=0o700)
    budgets = raw["budgets"]
    started = time.monotonic()
    prior_handler = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(budgets["wall_seconds_per_node"])
    try:
        packages = [p for p in raw["packages"] if p["node"] == node]
        total_bytes = verify_packages(packages)
        verified_at = time.monotonic()
        import duckdb

        with tempfile.TemporaryDirectory(prefix="adapter-tmp-", dir=output) as staging:
            connection = duckdb.connect(":memory:")
            try:
                for setting, value in (
                    ("threads", budgets["threads"]),
                    ("memory_limit", budgets["memory_limit"]),
                    ("max_temp_directory_size", budgets["tmp_limit"]),
                    ("temp_directory", staging),
                ):
                    connection.execute(f"SET {setting}=?", [value])
                daily = [probe_package(connection, package) for package in packages]
                tmp_bytes = sum(p.stat().st_size for p in Path(staging).rglob("*") if p.is_file())
            finally:
                connection.close()
        aggregate = {k: sum(r[k] for r in daily) for k in daily[0] if k != "market_date"}
        result = {
            "schema_version": "quanthus.p3-daily-adapter-probe-receipt.v1",
            "request_sha256": REQUEST_SHA,
            "code_revision": code_revision,
            "node": node,
            "disposition": "INPUT_ONLY_ADAPTER_EVIDENCE"
            if aggregate["unknown_basis_rows"] == 0
            else "NO_PASS_UNKNOWN_PRICE_BASIS",
            "code_receipt_sha256": sha256(receipt_path),
            "daily": daily,
            "aggregate": aggregate,
            "stages": [
                {
                    "stage": "verify_frozen_bytes",
                    "files": len(packages) * 2,
                    "bytes": total_bytes,
                    "elapsed_seconds": round(verified_at - started, 6),
                },
                {
                    "stage": "one_1d_tmp_per_package",
                    "disposition": "built_once_reused_for_all_counts",
                    "rows": aggregate["raw_1d_rows"],
                    "tmp_bytes_at_close": tmp_bytes,
                    "tmp_removed": True,
                    "elapsed_seconds": round(time.monotonic() - verified_at, 6),
                },
            ],
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "raw_times_changed": False,
            "economic_availability_inferred": False,
            "corrected_bundle_built": False,
            "candidate_scan": False,
            "strategy_or_labels": False,
            "B_accepted": False,
            "D_activated": False,
            "P6_or_formal_data_mutated": False,
            "data_issues_closed": False,
        }
        result["result_digest"] = digest_value(result)
        if len(canonical_json_bytes(result)) + 1 > budgets["output_max_bytes"]:
            raise ValueError("ADAPTER_OUTPUT_BUDGET_EXCEEDED")
        _write(output / "receipt.json", result)
        return result
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prior_handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--node", choices=("OCI", "CN01"), required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--prepare-code-receipt", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            (prepare_code_receipt if args.prepare_code_receipt else build)(
                repo=args.repository_root, node=args.node, code_revision=args.code_revision
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
