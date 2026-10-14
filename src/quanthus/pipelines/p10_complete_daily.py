"""Compose qualified daily + native 1m/5m into a self-contained typed DWD increment.

The daily member remains independently consumable. Joint admission never relabels
old Daily, changes Base, or grants execution. Minute knowledge times retain actual
acquisition and the later daily reference knowledge, never the business date.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.p10_complete_daily_schedule import CST, POLICY_ID, policy
from quanthus.pipelines.p10_delivery_common import BASE23_CONTENT as BASE_CONTENT
from quanthus.pipelines.p10_delivery_common import BASE23_MANIFEST as BASE_SHA
from quanthus.pipelines.p10_delivery_common import COMPLETE_KIND as KIND
from quanthus.pipelines.p10_delivery_common import COMPLETE_MINUTE_FILE as MINUTE_FILE
from quanthus.pipelines.p10_delivery_common import COMPLETE_POINTER as POINTER
from quanthus.pipelines.p10_delivery_common import (
    atomic,
    digest,
    encoded,
    file_digest,
    object_at,
    require_revision,
    safe_path,
    seal,
    verify_seal,
)
from quanthus.pipelines.p10_native_minute_delivery import ALLOWED, reference
from quanthus.pipelines.p10_native_minute_delivery import verify as verify_minute
from quanthus.pipelines.securities_warehouse.daily_core_domain import verify_daily_domain_increment


def copy_sealed_file(source: str, destination: str) -> str:
    """Share verified immutable member bytes; cross-device copies retain the same seal."""
    if Path(source).is_symlink() or not Path(source).is_file():
        raise ValueError("sealed member file must be a regular non-symlink")
    try:
        os.link(source, destination, follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS, errno.EMLINK}:
            raise
        shutil.copy2(source, destination)
    return destination


def daily_mapping(root: Path, day: str) -> tuple[dict[str, dict[str, Any]], Any]:
    import pyarrow.parquet as pq

    result = {}
    schema = None
    for path in sorted(root.glob("payload/*/dwd_security_bar/market_date=*/part-*.parquet")):
        file = pq.ParquetFile(path)
        schema = file.schema_arrow
        for row in file.read().to_pylist():
            match = re.search(
                r"#code=([0-9]{6})&date=(\d{4}-\d{2}-\d{2})$", row["source_record_id"]
            )
            if (
                match is None
                or match[2] != day
                or row["interval_size"] != "1d"
                or row["price_basis"] != "none"
                or match[1] in result
            ):
                raise ValueError("daily mapping lacks exact date/security/none identity")
            result[match[1]] = row
    manifest = object_at(root / "manifest.json")
    universe = manifest.get("acquisition_universe")
    expected = set(universe["codes"]) if universe else None
    if (
        universe
        and expected is not None
        and (
            universe.get("market_date") != day
            or universe.get("policy_id") != "p10-acquisition-prevention-v1"
            or universe.get("universe_digest") != digest(encoded(sorted(expected)))
            or universe.get("population") != len(expected)
        )
    ):
        raise ValueError("daily DWD universe identity differs")
    if expected is not None and set(result) != expected:
        missing = expected - set(result)
        for path in sorted(
            root.glob("payload/*/dwd_tradability_state/market_date=*/part-*.parquet")
        ):
            for row in pq.ParquetFile(path).read().to_pylist():
                match = re.search(
                    r"#code=([0-9]{6})&date=(\d{4}-\d{2}-\d{2})$", row["source_record_id"]
                )
                if match and match[1] in missing:
                    if (
                        match[2] != day
                        or row["is_tradable"] is not False
                        or row["can_buy"] is not False
                        or row["can_sell"] is not False
                        or match[1] in result
                    ):
                        raise ValueError("missing daily bar lacks exact suspended DWD identity")
                    result[match[1]] = {
                        **row,
                        "daily_price_observation_state": "unavailable_suspended",
                    }
    if schema is None or (set(result) != expected if expected is not None else len(result) != 4992):
        raise ValueError("daily DWD listing/session mapping population differs")
    return result, schema


def projected_rows(member: Path, interval: str, mapping: dict[str, dict[str, Any]]) -> Any:
    import pyarrow.parquet as pq

    statuses = {
        value["stock_code"]: value
        for value in (
            json.loads(line) for line in (member / "quality-status.jsonl").read_text().splitlines()
        )
    }
    provider = "tencent-mkline-m1" if interval == "1m" else "baostock-native-5m"
    # One immutable member identity shared by every projected row in this pass.
    source_revision = provider + "@" + file_digest(member / "manifest.json")[7:19]
    for batch in pq.ParquetFile(member / "minute-real-observed.parquet").iter_batches(
        batch_size=20000
    ):
        for row in batch.to_pylist():
            code = row["stock_code"]
            daily = mapping[code]
            if daily.get("daily_price_observation_state") == "unavailable_suspended":
                raise ValueError("minute price row contradicts sealed no-price suspension")
            quality = statuses[code]
            end = (
                datetime.fromisoformat(row["source_ts"])
                .replace(tzinfo=CST)
                .astimezone(timezone.utc)
            )
            auction = row["exchange_session_phase"] == "auction"
            acquired = datetime.fromisoformat(quality["source_acquired_at_or_after"])
            if acquired.tzinfo is None or daily["knowledge_from"].tzinfo is None:
                raise ValueError("actual knowledge time must be timezone aware")
            knowledge = max(acquired, daily["knowledge_from"])
            yield {
                "record_source": provider,
                "source_record_id": f"{code}.json.gz#code={code}&ts={row['source_ts']}",
                "source_revision": source_revision,
                "source_artifact_sha256": row["raw_sha256"],
                "knowledge_from": knowledge,
                "knowledge_to": None,
                "observation_start": end
                if auction
                else end - timedelta(minutes=1 if interval == "1m" else 5),
                "observation_end": end,
                "listing_id": daily["listing_id"],
                "calendar_session_id": daily["calendar_session_id"],
                "interval_size": interval,
                "price_basis": "none",
                "currency": daily["currency"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume_shares"],
                "turnover": row["amount"],
                "trade_count": None,
                "quality_grade": row["quality_grade"],
                "exchange_session_phase": row["exchange_session_phase"],
                "original_source_observed": True,
            }


def minute_dwd_schema(daily_schema: Any) -> Any:
    import pyarrow as pa

    return (
        daily_schema.append(pa.field("quality_grade", pa.string()))
        .append(pa.field("exchange_session_phase", pa.string()))
        .append(pa.field("original_source_observed", pa.bool_()))
    )


def _check_daily(
    root: Path, historical_alignment: Any = None
) -> tuple[dict[str, Any], Any, dict[str, dict[str, Any]], Any]:
    result = verify_daily_domain_increment(root)
    manifest = object_at(root / "manifest.json")
    if (
        manifest.get("kind") != "cn01_daily_core_dwd_increment"
        or manifest.get("base", {}).get("manifest_sha256") != BASE_SHA
        or manifest.get("base", {}).get("content_digest") != BASE_CONTENT
    ):
        raise ValueError("daily member must actually bind accepted Base 2.3, never retag legacy")
    if historical_alignment is not None:
        from quanthus.pipelines.p10_base23_realign import verify_alignment

        verify_alignment(historical_alignment, root)
    elif date.fromisoformat(manifest["market_date"]) < date.fromisoformat(
        policy()["baseline_market_date"]
    ):
        raise ValueError("historical complete date requires its explicit one-off authority")
    mapping, schema = daily_mapping(root, manifest["market_date"])
    return manifest, result, mapping, schema


def build(
    *,
    daily: Path,
    m1: Path,
    m5: Path,
    output_root: Path,
    code_revision: str,
    historical_alignment: Any = None,
) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    daily_manifest, daily_result, mapping, schema = _check_daily(daily, historical_alignment)
    from quanthus.pipelines.p10_domain_continuation import verify as verify_domain

    domain_packages = []
    domain_root = os.environ.get("QUANTHUS_DOMAIN_CONTINUATION_ROOT")
    prevention = os.environ.get("HERMES_ODS_PREVENTION_POLICY") == "p10-acquisition-prevention-v1"
    if prevention and not domain_root:
        raise ValueError("prevention delivery requires explicit domain root")
    if domain_root:
        for domain in ("disclosure", "financial"):
            root = Path(domain_root) / domain
            pointers = [
                path
                for path in sorted((root / "date-pointers").glob("*.json"))
                if path.stem <= daily_manifest["market_date"]
            ]
            if domain == "financial":
                pointers = pointers[-1:]
            for path in pointers:
                pointer = object_at(path)
                package = safe_path(root, pointer["manifest_path"]).parent
                checked = verify_domain(package)
                if checked["manifest_sha256"] != pointer["manifest_sha256"]:
                    raise ValueError("complete domain pointer identity differs")
                domain_packages.append((domain + "-" + path.stem, package, checked))
    if prevention and not any(
        key == "disclosure-" + daily_manifest["market_date"]
        for key, _path, _value in domain_packages
    ):
        raise ValueError("required dated disclosure continuation or held ledger missing")
    domain_disposition = {
        "policy_id": "p10-acquisition-prevention-v1" if prevention else None,
        "disclosure": "verified_versions_or_explicit_held_ledger"
        if domain_packages
        else "not_configured_legacy",
        "financial": "qualified_source_native_only"
        if any(key.startswith("financial-") for key, _path, _value in domain_packages)
        else "unavailable_no_qualified_weekly_source",
        "F10_shareholders": "unavailable_no_consumer_grant",
        "all_business_domains_complete": False,
    }
    domain_identities = {key: value["manifest_sha256"] for key, _path, value in domain_packages}
    require_revision(code_revision)
    verified = {interval: verify_minute(path) for interval, path in {"1m": m1, "5m": m5}.items()}
    source_sha = daily_manifest["selected_releases"]["market_daily_core"]["manifest_sha256"]
    for interval, member in verified.items():
        ref = reference((m1 if interval == "1m" else m5) / "daily-reference.json")
        member_manifest = object_at((m1 if interval == "1m" else m5) / "manifest.json")
        if (
            member_manifest.get("historical_alignment")
            and member_manifest["historical_alignment"]["daily_manifest_sha256"]
            != daily_result.manifest_sha256
        ):
            raise ValueError("historical native minute binds a different actual daily")
        if (
            member["market_date"] != daily_manifest["market_date"]
            or member["interval"] != interval
            or member["research_ready"] is not True
            or member["quality_grades"].get("R3", 0) != 0
            or ref["source_ods_manifest_sha256"] != source_sha
            or ref.get("event_ods_manifest_sha256")
            != daily_manifest["selected_releases"]["calendar_daily_events"]["manifest_sha256"]
            or set(ref["securities"]) != set(mapping)
        ):
            raise ValueError(
                "joint delivery requires exact daily population; "
                "both native intervals R2 minimum/R3=0"
            )
    if verified["1m"]["reference_digest"] != verified["5m"]["reference_digest"]:
        raise ValueError("minute members use different daily references")
    identities = {
        "1d": daily_result.manifest_sha256,
        **{interval: value["manifest_sha256"] for interval, value in verified.items()},
    }
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / ".publish.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        prior = object_at(output_root / POINTER) if (output_root / POINTER).exists() else None
        if prior and prior["market_date"] > daily_manifest["market_date"]:
            raise ValueError("no complete Daily pointer regression")
        binding = prior
        if prior and prior["market_date"] == daily_manifest["market_date"]:
            old = verify(safe_path(output_root, prior["manifest_path"]).parent)
            if (
                old["member_manifest_sha256"] == identities
                and old["code_revision"] == code_revision
                and old.get("domain_manifest_sha256", {}) == domain_identities
            ):
                return old
            # Same-day revisions bind the previous version, never overwrite its bytes.
        package_id = (
            "dwd-complete-"
            + daily_manifest["market_date"].replace("-", "")
            + "-"
            + digest(
                encoded(
                    [
                        identities,
                        domain_identities,
                        code_revision,
                        binding,
                        file_digest(policy_path()),
                    ]
                )
            )[7:23]
        )
        destination = output_root / "packages" / daily_manifest["market_date"] / package_id
        if destination.exists():
            result = verify(destination)
            publish(output_root, destination, result)
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        bytes_needed = sum(
            p.stat().st_size
            for member in (daily, m1, m5, *(path for _key, path, _value in domain_packages))
            for p in member.rglob("*")
            if p.is_file()
        )
        if shutil.disk_usage(output_root).free < 4 * 1024**3 + 3 * bytes_needed:
            raise ValueError("complete Daily insufficient staging space plus 4 GiB reserve")
        stage = Path(tempfile.mkdtemp(prefix=".complete-", dir=destination.parent))
        counts: Counter[str] = Counter()
        try:
            for interval, source_member in {"1d": daily, "1m": m1, "5m": m5}.items():
                shutil.copytree(
                    source_member, stage / "members" / interval, copy_function=copy_sealed_file
                )
            for key, package, _value in domain_packages:
                shutil.copytree(package, stage / "domains" / key, copy_function=copy_sealed_file)
            target = stage / MINUTE_FILE
            target.parent.mkdir(parents=True, exist_ok=True)
            projection_schema = minute_dwd_schema(schema)
            with pq.ParquetWriter(target, projection_schema, compression="zstd") as writer:
                for interval in ("1m", "5m"):
                    buffer = []
                    for row in projected_rows(stage / "members" / interval, interval, mapping):
                        counts[interval] += 1
                        buffer.append(row)
                        if len(buffer) >= 20000:
                            writer.write_table(
                                pa.Table.from_pylist(buffer, schema=projection_schema)
                            )
                            buffer.clear()
                    if buffer:
                        writer.write_table(pa.Table.from_pylist(buffer, schema=projection_schema))
            seal(
                stage,
                {
                    "schema_version": 1,
                    "kind": KIND,
                    "product_scope": "complete-daily-v1",
                    "package_id": package_id,
                    "market_date": daily_manifest["market_date"],
                    "code_revision": code_revision,
                    "policy_id": POLICY_ID,
                    "policy_sha256": file_digest(policy_path()),
                    **(
                        {"historical_alignment": historical_alignment}
                        if historical_alignment is not None
                        else {}
                    ),
                    "base": daily_manifest["base"],
                    "member_manifest_sha256": identities,
                    "domain_manifest_sha256": domain_identities,
                    "domain_disposition": domain_disposition,
                    "all_business_domains_complete": False,
                    "prior": binding,
                    "consumable_intervals": ["1d", "1m", "5m"],
                    "minute_row_counts": dict(counts),
                    "minute_quality_grades": {
                        interval: value["quality_grades"] for interval, value in verified.items()
                    },
                    "auction_time_semantics": "observed_0930_point_not_a_fabricated_0929_bar",
                    "minute_business_key": [
                        "listing_id",
                        "observation_start",
                        "interval_size",
                        "price_basis",
                        "exchange_session_phase",
                    ],
                    "daily_covered_after_date": daily_manifest["covered_after_date"],
                    "consumer_purpose": "gap_aware_non_execution_development",
                    "execution_authorized": False,
                    "limitations": [
                        "R2_real_gaps_preserved",
                        "Tencent_turnover_unavailable_null",
                        "Base23_seven_September_minute_source_gaps_unchanged",
                        "4173_visibility_unavailable_and_P3_P5_P6_gates_preserved",
                    ],
                    "sealed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            result = verify(stage)
            os.replace(stage, destination)
            publish(output_root, destination, result)
            return result
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def policy_path() -> Path:
    from quanthus.pipelines.p10_complete_daily_schedule import CONFIG

    return CONFIG


def verify(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    manifest = verify_seal(root, KIND)
    from quanthus.pipelines.p10_domain_continuation import verify as verify_domain

    for key, sha in manifest.get("domain_manifest_sha256", {}).items():
        domain = verify_domain(safe_path(root, "domains/" + key))
        if domain["manifest_sha256"] != sha or domain["market_date"] > manifest["market_date"]:
            raise ValueError("complete physical domain identity differs")
    require_revision(manifest.get("code_revision"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("product_scope") != "complete-daily-v1"
        or manifest.get("policy_id") != POLICY_ID
        or manifest.get("policy_sha256") != file_digest(policy_path())
        or manifest.get("consumable_intervals") != ["1d", "1m", "5m"]
        or manifest.get("consumer_purpose") != "gap_aware_non_execution_development"
        or manifest.get("execution_authorized") is not False
    ):
        raise ValueError("complete Daily purpose/quality policy differs")
    dm, dr, mapping, schema = _check_daily(
        root / "members/1d", manifest.get("historical_alignment")
    )
    if dm["market_date"] != manifest["market_date"] or dm["base"] != manifest["base"]:
        raise ValueError("complete Daily actual Base/date differs")
    source_sha = dm["selected_releases"]["market_daily_core"]["manifest_sha256"]
    refs = set()
    for interval in ("1m", "5m"):
        member = root / "members" / interval
        actual = verify_minute(member)
        ref = reference(member / "daily-reference.json")
        member_manifest = object_at(member / "manifest.json")
        if (
            member_manifest.get("historical_alignment")
            and member_manifest["historical_alignment"]["daily_manifest_sha256"]
            != dr.manifest_sha256
        ):
            raise ValueError("historical native minute binds a different actual daily")
        if (
            actual["interval"] != interval
            or actual["market_date"] != manifest["market_date"]
            or not actual["research_ready"]
            or actual["quality_grades"].get("R3", 0)
            or actual["quality_grades"] != manifest["minute_quality_grades"][interval]
            or actual["manifest_sha256"] != manifest["member_manifest_sha256"][interval]
            or ref["source_ods_manifest_sha256"] != source_sha
            or ref.get("event_ods_manifest_sha256")
            != dm["selected_releases"]["calendar_daily_events"]["manifest_sha256"]
            or set(ref["securities"]) != set(mapping)
        ):
            raise ValueError("complete Daily native member/date/daily population not qualified")
        refs.add(ref["reference_digest"])
    if len(refs) != 1 or dr.manifest_sha256 != manifest["member_manifest_sha256"]["1d"]:
        raise ValueError("complete Daily member identity/reference differs")
    parquet = pq.ParquetFile(root / MINUTE_FILE)
    if parquet.schema_arrow != minute_dwd_schema(schema):
        raise ValueError("complete Daily typed Decimal/time schema differs")
    actual_rows = (
        row for batch in parquet.iter_batches(batch_size=20000) for row in batch.to_pylist()
    )
    counts: Counter[str] = Counter()
    keys = set()
    for interval in ("1m", "5m"):
        for expected in projected_rows(root / "members" / interval, interval, mapping):
            physical_row = next(actual_rows, None)
            if physical_row is None or physical_row != expected:
                raise ValueError("typed minute DWD does not preserve source/time/Decimal/lineage")
            key = (
                physical_row["listing_id"],
                physical_row["observation_start"],
                physical_row["interval_size"],
                physical_row["price_basis"],
                physical_row["exchange_session_phase"],
                physical_row["knowledge_from"],
            )
            if key in keys or physical_row["quality_grade"] not in ALLOWED:
                raise ValueError("duplicate business/version minute key or rejected source")
            keys.add(key)
            counts[interval] += 1
    if next(actual_rows, None) is not None or dict(counts) != manifest["minute_row_counts"]:
        raise ValueError("complete Daily physical minute census differs")
    return {
        "package_id": manifest["package_id"],
        "market_date": manifest["market_date"],
        "manifest_sha256": file_digest(root / "manifest.json"),
        "content_digest": manifest["content_digest"],
        "base": manifest["base"],
        "member_manifest_sha256": manifest["member_manifest_sha256"],
        "code_revision": manifest["code_revision"],
        "minute_row_counts": dict(counts),
        "consumable_intervals": manifest["consumable_intervals"],
        "minute_quality_grades": manifest["minute_quality_grades"],
        "prior": manifest["prior"],
        "domain_manifest_sha256": manifest.get("domain_manifest_sha256", {}),
    }


def publish(root: Path, package: Path, verified: dict[str, Any]) -> dict[str, Any]:
    from quanthus.pipelines.p10_delivery_common import prepare_export

    prepare_export(root, package)
    pointer = {
        "schema_version": 1,
        "kind": "cn01_complete_daily_dwd_current",
        **{
            key: verified[key]
            for key in ("package_id", "market_date", "manifest_sha256", "content_digest", "base")
        },
        "manifest_path": (package / "manifest.json").relative_to(root).as_posix(),
    }
    dated = root / "date-pointers" / f"{verified['market_date']}.json"
    if dated.exists():
        old = object_at(dated)
        if old != pointer and verified["prior"] != old:
            raise ValueError("same-date revision must bind exact prior pointer")
    receipt_path = root / "verification" / f"{verified['manifest_sha256'][7:]}.json"
    if not receipt_path.exists():
        atomic(
            receipt_path,
            {
                **verified,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "mode": "actual_complete_package_files_and_native_projection",
            },
        )
    atomic(dated, pointer)
    atomic(root / POINTER, pointer)
    return pointer


def accepted(root: Path, target: date) -> dict[str, Any]:
    """Reuse actual acceptance only for the identical sealed manifest, not READY alone."""
    pointer = object_at(root / "date-pointers" / f"{target}.json")
    package = safe_path(root, pointer["manifest_path"]).parent
    manifest = object_at(package / "manifest.json")
    receipt = object_at(root / "verification" / f"{pointer['manifest_sha256'][7:]}.json")
    if (
        pointer["market_date"] != str(target)
        or receipt.get("mode") != "actual_complete_package_files_and_native_projection"
        or receipt.get("manifest_sha256") != pointer["manifest_sha256"]
        or receipt.get("content_digest") != pointer["content_digest"]
        or file_digest(package / "manifest.json") != pointer["manifest_sha256"]
        or (package / "READY").read_text().strip() != pointer["manifest_sha256"]
        or manifest.get("policy_id") != POLICY_ID
        or manifest.get("policy_sha256") != file_digest(policy_path())
        or manifest.get("consumable_intervals") != ["1d", "1m", "5m"]
    ):
        raise ValueError("complete Daily acceptance cannot be reused for changed identity")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path)
    parser.add_argument("--m1", type=Path)
    parser.add_argument("--m5", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--code-revision")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--historical-alignment", type=Path)
    args = parser.parse_args()
    result = (
        verify(args.verify)
        if args.verify
        else build(
            daily=args.daily,
            m1=args.m1,
            m5=args.m5,
            output_root=args.output_root,
            code_revision=args.code_revision,
            historical_alignment=object_at(args.historical_alignment)
            if args.historical_alignment
            else None,
        )
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
