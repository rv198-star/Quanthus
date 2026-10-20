"""Bounded, volume-only processing revision of an existing Daily ODS release.

Original observations remain local evidence. Only selected Tencent fallback
share volumes are replaced from newly observed, lossless BaoStock daily SDK
responses. OHLC, amount, status and valuation are never silently replaced.
This is new knowledge, not historical provider acquisition or PIT admission.
"""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import importlib
import json
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.pipelines.contracts import verify_ods_release

from .base22 import canonical, file_sha
from .base22_sources import _tree, disjoint_output, physical_path
from .daily_base24_market_resume import FIELDS, stamp

CONTRACT = "p10-cn02-exact-share-volume-revision-v1"


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def volume_row(
    before: dict[str, Any],
    capture: bytes,
    *,
    day: str,
    code: str,
    revision: str,
    built_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one complete captured response; return a new row and safe proof."""
    envelope = json.loads(gzip.decompress(capture))
    raw = base64.b64decode(envelope["raw_base64"], validate=True)
    if (
        envelope.get("policy_id") != "p10-acquisition-prevention-v1"
        or envelope.get("status") != "0"
        or envelope.get("raw_sha256") != hashlib.sha256(raw).hexdigest()
        or envelope.get("raw_bytes") != len(raw)
    ):
        raise ValueError("PRECISION_CAPTURE_CLOSURE_INVALID")
    request = envelope["request"]
    vendor = ("sh." if code.startswith("6") else "sz.") + code
    required = dict(
        provider="baostock",
        api="query_history_k_data_plus",
        code=vendor,
        fields=FIELDS,
        frequency="d",
        adjustflag="3",
    )
    if any(request.get(k) != v for k, v in required.items()) or not (
        request["start_date"] <= day <= request["end_date"]
        and (
            date.fromisoformat(request["end_date"]) - date.fromisoformat(request["start_date"])
        ).days
        <= 4
    ):
        raise ValueError("PRECISION_CAPTURE_REQUEST_DIFFERS")
    observed = envelope["warehouse_observed_at"]
    built_at = built_at or datetime.now(timezone.utc).isoformat()
    if not (
        stamp(envelope["request_started_at"])
        <= stamp(envelope["response_completed_at"])
        <= stamp(observed)
        and stamp(observed) >= stamp(before["acquired_at"])
        and stamp(built_at) >= stamp(observed)
    ):
        raise ValueError("PRECISION_KNOWLEDGE_CLOCK_INVALID")
    response = json.loads(raw)
    if response.get("error_code") != "0" or response.get("fields") != FIELDS.split(","):
        raise ValueError("PRECISION_CAPTURE_RESPONSE_DIFFERS")
    rows = response.get("rows")
    if (
        not isinstance(rows, list)
        or len(rows) > 3
        or any(
            not isinstance(row, list)
            or len(row) != 16
            or any(not isinstance(token, str) for token in row)
            or row[1] != vendor
            or not request["start_date"] <= row[0] <= request["end_date"]
            for row in rows
        )
        or len({row[0] for row in rows}) != len(rows)
    ):
        raise ValueError("PRECISION_CAPTURE_ROW_KEYS_INVALID")
    matching = [row for row in rows if row[0] == day]
    if len(matching) != 1:
        raise ValueError("PRECISION_TARGET_DATE_ABSENT")
    row = matching[0]
    volume = Decimal(row[6])
    if (
        not volume.is_finite()
        or volume < 0
        or volume != volume.to_integral_value()
        or row[10] != "1"
        or before.get("stock_code") != code
        or before.get("security_id") != code
        or before.get("market_date") != day
        or before.get("date") != day
        or before.get("adj_mode") != "none"
        or "tencent" not in str(before.get("source", "")).lower()
        or not Decimal(before["volume"]).is_finite()
    ):
        raise ValueError("PRECISION_TARGET_SCOPE_OR_VOLUME_INVALID")
    # Keep the daily OHLC and amount exactly as observed, including their scale.
    after = {
        **before,
        "volume": row[6],
        "source": CONTRACT,
        "acquired_at": observed,
        "built_at": built_at,
    }
    proof = dict(
        stock_code=code,
        market_date=day,
        contract=CONTRACT,
        adapter_revision=revision,
        before_row_sha256=_sha(canonical(before)),
        after_row_sha256=_sha(canonical(after)),
        capture_sha256=_sha(capture),
        original_daily_observed_at=before["acquired_at"],
        new_volume_observed_at=observed,
        provider_availability_at=None,
        captured_provider_availability_at=envelope.get("provider_availability_at"),
        publication_at=envelope.get("publication_at"),
        changed_fields=sorted(k for k in before if before[k] != after[k]),
        volume_value_changed=Decimal(before["volume"]) != volume,
        original_source=before["source"],
        original_nonvolume_fields_preserved=True,
        field_source_rule=(
            "volume: new captured SDK string; all business fields otherwise: prior row"
        ),
        historical_pit_authorized=False,
    )
    return after, proof


def build_market_revision(
    *,
    source: Path,
    expected_source_sha256: str,
    captures: dict[str, Path],
    expected_capture_hashes: dict[str, str],
    output: Path,
    revision: str,
) -> dict[str, Any]:
    """Copy one verified source and rewrite only its bounded daily-bar partition.

    The old row file, original manifest and every new capture are separate
    manifest members. READY is last. Failed output stays evidence, never reused.
    """
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")

    source = physical_path(source)
    captures = {code: physical_path(p) for code, p in captures.items()}
    disjoint_output(output, [source, *captures.values()])
    output = physical_path(output)
    if (
        output.exists()
        or not captures
        or len(captures) > 3000
        or (set(captures) != set(expected_capture_hashes))
        or len(revision) != 40
    ):
        raise ValueError("PRECISION_NEW_BOUNDED_OUTPUT_REQUIRED")
    verified = verify_ods_release(source)
    if verified.manifest_sha256 != expected_source_sha256:
        raise ValueError("PRECISION_SOURCE_CHANGED")
    original = json.loads((source / "release.json").read_bytes())
    day = original["market_date"]
    artifacts = {a["name"]: a for a in original["artifacts"]}
    if original["schedule"] != "daily" or "daily_ohlcv_none_delta" not in artifacts:
        raise ValueError("PRECISION_DAILY_MARKET_SOURCE_REQUIRED")
    declared = {"release.json", "READY"} | {a["relative_path"] for a in original["artifacts"]}
    if _tree(source) != declared:
        raise ValueError("PRECISION_SOURCE_PHYSICAL_SET_DIFFERS")
    bar_path = source / artifacts["daily_ohlcv_none_delta"]["relative_path"]
    pf = pq.ParquetFile(bar_path)
    if pf.metadata.num_rows > 6000:
        raise ValueError("PRECISION_PARTITION_ROW_BUDGET")
    rows = pf.read().to_pylist()
    codes = [row["stock_code"] for row in rows]
    if len(set(codes)) != len(rows) or not set(captures) <= set(codes):
        raise ValueError("PRECISION_SELECTION_KEYS_INVALID")
    frozen = {str(p): file_sha(p) for p in [source / name for name in declared]}
    frozen.update({str(p): file_sha(p) for p in captures.values()})
    replacements, proofs = {}, []
    for before in rows:
        code = before["stock_code"]
        if code not in captures:
            continue
        path = captures[code]
        if file_sha(path) != expected_capture_hashes[code]:
            raise ValueError("PRECISION_CAPTURE_CHANGED")
        after, proof = volume_row(before, path.read_bytes(), day=day, code=code, revision=revision)
        replacements[code] = after
        proofs.append(proof)
    # No source access or row validation failure can create the output directory.
    shutil.copytree(
        source,
        output,
        copy_function=shutil.copyfile,
        ignore=shutil.ignore_patterns("READY", "release.json"),
    )
    meta = output / "artifacts/precision-revision"
    meta.mkdir()
    shutil.copyfile(source / "release.json", meta / "prior-release.json")
    shutil.copyfile(bar_path, meta / "prior-daily-bars.parquet")
    for code, path in sorted(captures.items()):
        shutil.copyfile(path, meta / (code + ".json.gz"))
    new_rows = [replacements.get(row["stock_code"], row) for row in rows]
    new_bar = output / artifacts["daily_ohlcv_none_delta"]["relative_path"]
    pq.write_table(
        pa.Table.from_pylist(new_rows, schema=pf.schema_arrow), new_bar, compression="zstd"
    )
    if pq.ParquetFile(new_bar).read().to_pylist() != new_rows:
        raise ValueError("PRECISION_SERIALIZED_VALUES_DIFFER")
    report = dict(
        kind=CONTRACT,
        source_manifest_sha256=expected_source_sha256,
        adapter_revision=revision,
        market_date=day,
        selected=len(proofs),
        unchanged_rows=len(rows) - len(proofs),
        rows=proofs,
        historical_pit_authorized=False,
        execution_authorized=False,
        consumer_admission="NOT_GRANTED",
        revision_is_new_knowledge=True,
    )
    _write(meta / "revision.safe.json", report)
    now = datetime.now(timezone.utc).isoformat()
    manifest = copy.deepcopy(original)
    manifest.update(
        release_id="hermes-market-daily-core-"
        + day.replace("-", "")
        + "-exact-volume-"
        + revision[:12]
        + "-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        created_at=now,
        producer=CONTRACT + "@" + revision,
    )
    changed = next(a for a in manifest["artifacts"] if a["name"] == "daily_ohlcv_none_delta")
    changed.update(
        sha256=file_sha(new_bar),
        size_bytes=new_bar.stat().st_size,
        source_revision=CONTRACT + "@" + revision,
        knowledge_cutoff=now,
    )
    for i, p in enumerate(sorted(meta.iterdir())):
        manifest["artifacts"].append(
            dict(
                name="precision_revision_evidence_" + str(i),
                relative_path=p.relative_to(output).as_posix(),
                sha256=file_sha(p),
                size_bytes=p.stat().st_size,
                source_revision=CONTRACT + "@" + revision,
                knowledge_cutoff=now,
            )
        )
    _write(output / "release.json", manifest)
    for name, expected in frozen.items():
        if file_sha(Path(name)) != expected:
            raise ValueError("PRECISION_INPUT_CHANGED_DURING_BUILD")
    identity = file_sha(output / "release.json")
    with (output / "READY").open("x") as stream:
        stream.write(identity + "\n")
    verify_market_revision(output)
    return dict(
        output=str(output),
        manifest_sha256=identity,
        selected=len(proofs),
        unchanged_rows=len(rows) - len(proofs),
        inputs_unchanged=True,
        source_values_exported=False,
        new_processing_revision=True,
    )


def verify_market_revision(root: Path) -> dict[str, Any]:
    """Offline replay of the full allowed-change set, including unchanged files."""
    pq = importlib.import_module("pyarrow.parquet")
    root = physical_path(root)
    verified = verify_ods_release(root)
    manifest = json.loads((root / "release.json").read_bytes())
    meta = root / "artifacts/precision-revision"
    prior_bytes = (meta / "prior-release.json").read_bytes()
    prior = json.loads(prior_bytes)
    evidence = json.loads((meta / "revision.safe.json").read_bytes())
    if (
        evidence.get("kind") != CONTRACT
        or evidence.get("source_manifest_sha256") != _sha(prior_bytes)
        or evidence.get("market_date") != manifest["market_date"]
        or prior["market_date"] != manifest["market_date"]
        or evidence.get("historical_pit_authorized") is not False
        or evidence.get("execution_authorized") is not False
        or evidence.get("revision_is_new_knowledge") is not True
        or evidence.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise ValueError("PRECISION_REVISION_CONTRACT_DIFFERS")
    declared = {"READY", "release.json"} | {a["relative_path"] for a in manifest["artifacts"]}
    if _tree(root) != declared:
        raise ValueError("PRECISION_REVISION_PHYSICAL_SET_DIFFERS")
    artifacts = {a["name"]: a for a in manifest["artifacts"]}
    old_artifacts = {a["name"]: a for a in prior["artifacts"]}
    if not old_artifacts.keys() <= artifacts.keys():
        raise ValueError("PRECISION_PRIOR_ARTIFACT_ABSENT")
    for name, artifact in old_artifacts.items():
        if name != "daily_ohlcv_none_delta" and artifact != artifacts[name]:
            raise ValueError("PRECISION_UNRELATED_ARTIFACT_CHANGED")
    old_path = meta / "prior-daily-bars.parquet"
    if file_sha(old_path) != old_artifacts["daily_ohlcv_none_delta"]["sha256"]:
        raise ValueError("PRECISION_PRIOR_ROWS_CHANGED")
    new_path = root / artifacts["daily_ohlcv_none_delta"]["relative_path"]
    old_pf, new_pf = pq.ParquetFile(old_path), pq.ParquetFile(new_path)
    if old_pf.metadata.num_rows > 6000 or (
        old_pf.schema_arrow != new_pf.schema_arrow
        or old_pf.metadata.num_rows != new_pf.metadata.num_rows
    ):
        raise ValueError("PRECISION_ROW_SCHEMA_OR_COUNT_DIFFERS")
    before, after = old_pf.read().to_pylist(), new_pf.read().to_pylist()
    proofs = evidence["rows"]
    expected = {item["stock_code"]: item for item in proofs}
    if len(expected) != len(proofs) or not 0 < len(expected) <= 3000:
        raise ValueError("PRECISION_REVISION_KEYS_INVALID")
    visited = set()
    for old, new in zip(before, after, strict=True):
        code = old["stock_code"]
        if code in visited or new["stock_code"] != code:
            raise ValueError("PRECISION_REVISION_KEYS_INVALID")
        visited.add(code)
        if code not in expected:
            if new != old:
                raise ValueError("PRECISION_UNSELECTED_ROW_CHANGED")
            continue
        if stamp(new["built_at"]) > stamp(manifest["created_at"]):
            raise ValueError("PRECISION_KNOWLEDGE_CLOCK_INVALID")
        derived, proof = volume_row(
            old,
            (meta / (code + ".json.gz")).read_bytes(),
            day=manifest["market_date"],
            code=code,
            revision=evidence["adapter_revision"],
            built_at=new["built_at"],
        )
        if derived != new or proof != expected[code]:
            raise ValueError("PRECISION_ALLOWED_CHANGE_REPLAY_DIFFERS")
    if not expected.keys() <= visited or (
        evidence["selected"] != len(expected)
        or evidence["unchanged_rows"] != len(before) - len(expected)
    ):
        raise ValueError("PRECISION_SELECTION_CENSUS_DIFFERS")
    return dict(
        status="PASS_OFFLINE_VOLUME_REVISION_REPLAY",
        selected=len(expected),
        unchanged_rows=len(before) - len(expected),
        manifest_sha256=verified.manifest_sha256,
        external_sources_opened=False,
    )
