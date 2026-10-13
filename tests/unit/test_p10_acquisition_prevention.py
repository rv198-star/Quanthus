from __future__ import annotations

import gzip
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.p10_delivery_common import digest, encoded
from quanthus.pipelines.p10_domain_continuation import disclosure_candidates
from quanthus.pipelines.p10_native_minute_delivery import reference
from quanthus.providers.acquisition_evidence import POLICY, capture, decimal_token, verify_capture
from quanthus.providers.common import write_parquet
from quanthus.providers.security_universe import validate


def test_raw_response_is_lossless_and_clocks_are_separate(tmp_path: Path) -> None:
    raw = b'{"value":"123.4567000000","unchanged":true}'
    receipt = capture(
        root=tmp_path,
        request={"page": 1},
        raw=raw,
        started_at="2026-10-13T01:00:00+00:00",
        completed_at="2026-10-13T01:00:01+00:00",
        status="200",
    )
    envelope = verify_capture(tmp_path / receipt["capture_file"])
    assert envelope["publication_at"] is None
    assert envelope["provider_availability_at"] is None
    assert envelope["raw_sha256"] == digest(raw)[7:]
    assert envelope["response_completed_at"] != envelope["request_started_at"]


@pytest.mark.parametrize("token", [None, "", "nan", "inf", "broken", "1.00000000001", "1e28"])
def test_invalid_numeric_is_never_fabricated_zero(token: object) -> None:
    with pytest.raises(ValueError):
        decimal_token(token, required=True)


def test_decimal_token_preserves_supplier_precision() -> None:
    assert decimal_token("123.4567000000") == "123.4567000000"
    assert Decimal(decimal_token("123.4567000000")) == Decimal("123.4567")
    assert decimal_token(None) is None
    assert decimal_token("0") == "0"


def test_authoritative_target_universe_keeps_suspension_and_new_listing() -> None:
    daily = [{"code": "sh.688001", "tradeStatus": "0", "code_name": "synthetic"}]
    basic = [{"code": "sh.688001", "type": "1", "ipoDate": "2026-10-09", "outDate": ""}]
    rows = validate(date(2026, 10, 12), daily, basic)
    assert rows[0]["tradestatus"] == "0"
    assert rows[0]["list_date"] == "2026-10-09"
    assert rows[0]["target_date"] == "2026-10-12"
    with pytest.raises(ValueError, match="LISTING_DATE_CONFLICT"):
        validate(date(2026, 10, 8), daily, basic)
    with pytest.raises(ValueError, match="AUTHORITY_MISSING"):
        validate(date(2026, 10, 12), daily, [])


def test_late_and_metadata_revisions_only_not_old_snapshot_duplicates() -> None:
    template = {
        "stock_code": "000001",
        "title": "synthetic",
        "publish_time": "2026-10-09 10:00:00",
        "announce_date": "2026-10-09",
    }
    rows = [
        {**template, "ann_id": "new"},
        {**template, "ann_id": "changed", "title": "revision"},
        {**template, "ann_id": "old"},
        {**template, "ann_id": "today", "announce_date": "2026-10-12"},
    ]
    prior = {"changed": template, "old": template}
    selected = disclosure_candidates(rows, prior, date(2026, 10, 9))
    assert [(row["ann_id"], row["correction_kind"]) for row in selected] == [
        ("new", "late_observation"),
        ("changed", "metadata_revision"),
    ]
    assert all(row["announce_date"] == "2026-10-09" for row in selected)
    with pytest.raises(ValueError, match="DUPLICATE"):
        disclosure_candidates([rows[0], rows[0]], {}, date(2026, 10, 9))


def test_source_reversion_is_kept_even_when_old_dwd_matches_current() -> None:
    row = {
        "stock_code": "000001",
        "ann_id": "reverted",
        "title": "synthetic",
        "publish_time": "2026-10-08 16:32:29",
        "announce_date": "2026-10-08",
    }
    old_dwd = {"reverted": row}
    previous_source = {"reverted": {**row, "publish_time": "2026-10-08 16:32:30"}}
    selected = disclosure_candidates([row], old_dwd, date(2026, 10, 12), previous_source)
    assert selected[0]["correction_kind"] == "metadata_revision"
    assert selected[0]["publish_time"] == row["publish_time"]


def test_prevention_cannot_reuse_old_ready_without_refreshed_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from quanthus.pipelines.securities_warehouse import daily_core_ods_catalog as catalogs

    day = date(2026, 10, 13)
    path = tmp_path / "daily/2026-10-13/legacy"
    path.mkdir(parents=True)
    release = SimpleNamespace(
        schedule="daily",
        market_date=day,
        created_at="synthetic",
        release_id="hermes-market-daily-core-synthetic",
        artifacts=[],
    )
    checked = SimpleNamespace(release=release)
    monkeypatch.setattr(catalogs, "verify_ods_release", lambda p: checked)
    monkeypatch.delenv("HERMES_ODS_PREVENTION_POLICY", raising=False)
    assert "market_daily_core" in catalogs.find_ready_daily_ods_lanes(
        release_root=tmp_path, market_date=day
    )
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", POLICY)
    assert "market_daily_core" not in catalogs.find_ready_daily_ods_lanes(
        release_root=tmp_path, market_date=day
    )
    release.artifacts = [
        SimpleNamespace(name=n, relative_path=Path(n + ".json"))
        for n in ["source_universe", "acquisition_quality", "original_responses"]
    ]
    for n in ["source_universe", "acquisition_quality"]:
        (path / (n + ".json")).write_text(
            json.dumps({"policy_id": POLICY, "market_date": str(day)})
        )
    assert "market_daily_core" in catalogs.find_ready_daily_ods_lanes(
        release_root=tmp_path, market_date=day
    )


def test_new_minute_job_refuses_legacy_daily_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.pipelines import p10_native_minute_delivery as minutes

    (tmp_path / "release.json").write_text(json.dumps({"artifacts": []}))
    monkeypatch.setattr(minutes, "_reference", lambda *args: ({}, "synthetic", {}))
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", POLICY)
    with pytest.raises(ValueError, match="refreshed acquisition universe"):
        minutes.freeze_reference(tmp_path, "2026-10-13", tmp_path / "reference.json")


def test_compacted_concept_projection_matches_full_original_projection(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.p10_domain_continuation import _compact_concept_witnesses
    from quanthus.pipelines.securities_warehouse.financial_reporting import (
        _finalize_concepts,
        financial_reporting_schemas,
    )

    schemas = financial_reporting_schemas()
    schema = schemas["dwd_financial_concept"]
    base = {name: None for name in schema.names}
    base.update(
        record_source="synthetic",
        source_revision="v1",
        source_artifact_sha256="a" * 64,
        financial_concept_id="concept",
        taxonomy_namespace="synthetic",
        taxonomy_version="v1",
        concept_code="field",
        concept_name="field",
        statement_type="balance",
        data_type="provider_native_scalar",
        period_type="instant",
    )
    values = []
    for acquired, starts in [(10, [8, 9, 8]), (11, [7, 8]), (12, [9, 10])]:
        for n, start in enumerate(starts):
            values.append(
                {
                    **base,
                    "source_record_id": str(n),
                    "knowledge_from": datetime(2026, 10, acquired, tzinfo=timezone.utc),
                    "effective_from": datetime(2026, 9, start, tzinfo=timezone.utc),
                }
            )
    original = tmp_path / "raw.parquet"
    pq.write_table(pa.Table.from_pylist(values, schema=schema), original)
    full, compact = tmp_path / "full", tmp_path / "compact"
    path = full / "staging/concepts/part-00-000000.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(original.read_bytes())
    compact.mkdir()
    assert _compact_concept_witnesses(compact, original, schema) == 3
    assert _finalize_concepts(full, {}, schemas) == _finalize_concepts(compact, {}, schemas) == 2
    relative = "payload/financial_reporting/dwd_financial_concept/part-00000.parquet"
    assert pq.ParquetFile(full / relative).read().equals(pq.ParquetFile(compact / relative).read())
    assert pq.ParquetFile(original).metadata.num_rows == 7


def test_new_reference_binds_dynamic_universe_not_lower_fixed_threshold(tmp_path: Path) -> None:
    from quanthus.pipelines.p10_complete_daily_schedule import POLICY_ID

    codes = {"000001": {"tradestatus": "1"}, "688001": {"tradestatus": "0"}}
    ref = {
        "kind": "p10_complete_daily_job_reference_v2",
        "policy_id": POLICY_ID,
        "population": 2,
        "securities": codes,
        "universe_policy_id": POLICY,
        "universe_digest": digest(encoded(sorted(codes))),
        "universe_manifest_sha256": "sha256:" + "a" * 64,
    }
    ref["reference_digest"] = digest(encoded(ref))
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(ref))
    assert reference(path)["population"] == 2
    ref["population"] = 1
    ref["reference_digest"] = digest(
        encoded({k: v for k, v in ref.items() if k != "reference_digest"})
    )
    path.write_text(json.dumps(ref))
    with pytest.raises(ValueError):
        reference(path)


def test_new_financial_payload_bytes_and_legacy_repr_both_hash_verified(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.financial_reporting import (
        decode_financial_envelope,
    )

    raw = encoded({"result": {"data": {"report_list": {}}}})
    packed = gzip.compress(raw, mtime=0)
    path = tmp_path / "raw.parquet"
    write_parquet([{"raw": packed}], path, pa.schema([("raw", pa.binary())]))
    assert pq.read_table(path).to_pylist()[0]["raw"] == packed
    for payload in [packed, repr(packed).encode("ascii")]:
        row = {
            "raw_payload_gzip": payload,
            "raw_payload_sha256": digest(raw),
            "raw_payload_bytes": len(raw),
        }
        assert decode_financial_envelope(row).raw_payload == raw
        row["raw_payload_sha256"] = "sha256:" + "b" * 64
        with pytest.raises(RuntimeError, match="SHA-256"):
            decode_financial_envelope(row)


def test_july_snapshot_cannot_claim_current_no_change(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.providers.ods_snapshot import collect

    pq.write_table(
        pa.Table.from_pylist([{"announce_date": "2026-07-18"}]),
        tmp_path / "dividend_history.parquet",
    )
    result = collect(
        "dividends", date(2026, 10, 9), date(2026, 10, 12), tmp_path / "out.parquet", tmp_path
    )
    assert result["selected_row_count"] == 0
    assert result["source_refreshed"] is False
    assert result["current_no_change_proven"] is False
    assert result["reason_code"] == "SOURCE_NOT_REFRESHED"
    assert result["provider_acquisition_at"] is None


def test_financial_continuation_reuses_all_three_original_envelopes(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.p10_domain_continuation import _write_financial
    from quanthus.providers.fundamentals import _payload_json

    rows, receipts = [], []
    for provider, statement in [
        ("fzb", "balance_sheet"),
        ("lrb", "income_statement"),
        ("llb", "cash_flow"),
    ]:
        raw = encoded(
            {
                "result": {
                    "data": {
                        "report_list": {
                            "20260630": {
                                "rCurrency": "CNY",
                                "rType": "consolidated",
                                "publish_date": "2026-08-01",
                                "data": [
                                    {
                                        "item_field": "SYNTHETIC",
                                        "item_title": "synthetic",
                                        "item_value": "12.500000",
                                    }
                                ],
                            }
                        }
                    }
                }
            }
        )
        row = {
            "raw_payload_gzip": repr(gzip.compress(raw, mtime=0)).encode("ascii"),
            "raw_payload_sha256": digest(raw),
            "raw_payload_bytes": len(raw),
            "security_id": "000001",
            "stock_code": "000001",
            "provider_source": provider,
            "statement_type": statement,
            "source": "sina.CompanyFinanceService.getFinanceReport2022",
            "source_revision": "synthetic",
            "acquired_at": "2026-10-10T09:00:00+08:00",
        }
        assert _payload_json(row)["result"]["data"]["report_list"]
        rows.append(row)
        receipts.append(
            {
                "dataset": "financial_statements",
                "security_id": "000001",
                "request_json": json.dumps({"params": {"source": provider}}),
                "status": "success_nonempty",
                "response_sha256": digest(raw),
            }
        )
    source = tmp_path / "source.parquet"
    receipt = tmp_path / "receipt.parquet"
    pq.write_table(pa.Table.from_pylist(rows), source)
    pq.write_table(pa.Table.from_pylist(receipts), receipt)
    start = datetime(1991, 1, 1, tzinfo=timezone.utc)
    common = {
        "security_id": "s1",
        "effective_from": start,
        "effective_to": None,
        "knowledge_from": start,
        "knowledge_to": None,
    }
    ids = {
        "dwd_security": [{**common, "company_id": "c1"}],
        "dwd_security_identifier": [
            {**common, "identifier_namespace": "CN_STOCK_CODE:SZSE", "identifier_value": "000001"}
        ],
    }
    observed = datetime(2026, 10, 13, tzinfo=timezone.utc)
    audit = _write_financial(
        tmp_path / "stage",
        source=source,
        receipts=receipt,
        identities=ids,
        observed=observed,
        market_date=date(2026, 10, 10),
    )
    assert audit["source_envelopes"] == 3
    assert audit["row_counts"]["dwd_financial_report"] == 3
    facts = pq.read_table(
        tmp_path / "stage/payload/dwd_financial_fact/part-00000.parquet"
    ).to_pylist()
    assert len(facts) == 3
    assert all(item["knowledge_from"] == observed for item in facts)
    assert all(item["value_decimal"] == Decimal("12.500000") for item in facts)
    receipts[0]["response_sha256"] = "sha256:" + "c" * 64
    pq.write_table(pa.Table.from_pylist(receipts), receipt)
    with pytest.raises(ValueError, match="CLOSURE_DIFFERS"):
        _write_financial(
            tmp_path / "bad",
            source=source,
            receipts=receipt,
            identities=ids,
            observed=observed,
            market_date=date(2026, 10, 10),
        )


def test_failure_drill_cannot_suppress_real_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quanthus.pipelines import p10_complete_daily_notifications as notifications

    monkeypatch.setattr(
        notifications.mail, "_send_resend", lambda **kwargs: {"email_id": "synthetic"}
    )
    day = date(2026, 10, 13)
    real = notifications.failure(day=day, stage="weekly", state_root=tmp_path, reason="real")
    drill = notifications.failure(
        day=day,
        stage="weekly",
        state_root=tmp_path,
        reason="drill",
        context={"isolated_failure_drill": "synthetic-test"},
    )
    assert real["idempotency_key"] != drill["idempotency_key"]
    assert (
        notifications.failure(day=day, stage="weekly", state_root=tmp_path, reason="again")[
            "status"
        ]
        == "already_sent"
    )


def test_provider_can_import_as_standalone_entrypoint() -> None:
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import quanthus.providers.security_universe"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("interrupt_at", ["export", "dated", "current"])
def test_orphan_sealed_package_recovery_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt_at: str
) -> None:
    from quanthus.pipelines import p10_domain_continuation as domain
    from quanthus.pipelines.p10_delivery_common import BASE23_CONTENT, BASE23_MANIFEST

    source = tmp_path / "source.parquet"
    source.write_bytes(b"synthetic-source")
    config = tmp_path / "config.json"
    config.write_text("{}")
    monkeypatch.setattr(
        domain,
        "_identity_rows",
        lambda *args: ({}, {"manifest_sha256": BASE23_MANIFEST, "content_digest": BASE23_CONTENT}),
    )
    monkeypatch.setattr(domain, "_history_bindings", lambda *args: [])
    calls = []

    def construct(*args: object, **kwargs: object) -> dict:
        calls.append(True)
        return {"row_counts": {"dwd_disclosure": 0, "dwd_disclosure_subject": 0}}

    monkeypatch.setattr(domain, "_write_disclosures", construct)
    real_atomic, real_export = domain.atomic, domain.prepare_export
    interrupted = False

    def fail_atomic(path: Path, value: object) -> None:
        nonlocal interrupted
        should_fail = (interrupt_at == "dated" and path.parent.name == "date-pointers") or (
            interrupt_at == "current" and path.name == "CURRENT_DOMAIN.json"
        )
        if should_fail and not interrupted:
            interrupted = True
            raise OSError("synthetic interruption")
        real_atomic(path, value)

    def fail_export(*args: object) -> None:
        nonlocal interrupted
        if interrupt_at == "export" and not interrupted:
            interrupted = True
            raise OSError("synthetic interruption")
        real_export(*args)

    monkeypatch.setattr(domain, "atomic", fail_atomic)
    monkeypatch.setattr(domain, "prepare_export", fail_export)
    args = dict(
        domain="disclosure",
        source=source,
        receipts=None,
        core=tmp_path / "core",
        products=tmp_path / "products",
        base=tmp_path / "base",
        config=config,
        day=date(2026, 10, 12),
        code_revision="a" * 40,
    )
    with pytest.raises(OSError, match="interruption"):
        domain.build(**args)
    result = domain.build(**args)
    repeated = domain.build(**args)
    assert result["manifest_sha256"] == repeated["manifest_sha256"]
    assert len(calls) == 1
    assert (tmp_path / "products/disclosure/CURRENT_DOMAIN.json").exists()


def test_financial_json_numeric_tokens_preserved_without_legacy_change() -> None:
    from quanthus.pipelines.securities_warehouse.financial_reporting import (
        decode_financial_envelope,
    )

    raw = (
        b'{"result":{"data":{"report_list":{"20260630":{"data":['
        b'{"item_value":123456789012345.1234567890}]}}}}}'
    )
    row = {
        "raw_payload_gzip": gzip.compress(raw, mtime=0),
        "raw_payload_sha256": digest(raw),
        "raw_payload_bytes": len(raw),
    }
    exact = decode_financial_envelope(row, preserve_numeric_tokens=True)
    assert exact.report_list["20260630"]["data"][0]["item_value"] == "123456789012345.1234567890"
    legacy = decode_financial_envelope(row)
    assert isinstance(legacy.report_list["20260630"]["data"][0]["item_value"], float)


def test_prelisting_disclosure_is_held_without_blocking_qualified_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines import p10_domain_continuation as domain

    core = tmp_path / "core"
    pointer = core / "date-pointers/2026-10-12.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps(
            {
                "market_date": "2026-10-12",
                "manifest_path": "not-read",
                "manifest_sha256": "synthetic",
            }
        )
    )
    monkeypatch.setattr(domain, "_previous_disclosures", lambda *args: {})
    source = tmp_path / "source.parquet"
    base = {
        "stock_code": "688001",
        "title": "synthetic",
        "publish_time": "2026-10-08 12:00:00",
        "announce_date": "2026-10-08",
        "url": "synthetic",
        "acquired_at": "2026-10-12T18:30:00+08:00",
    }
    pq.write_table(
        pa.Table.from_pylist(
            [
                {**base, "ann_id": "prelisting"},
                {
                    **base,
                    "ann_id": "qualified",
                    "announce_date": "2026-10-12",
                    "publish_time": "2026-10-12 12:00:00",
                },
            ]
        ),
        source,
    )
    ids = {
        "dwd_security": [{"security_id": "s1", "company_id": "c1"}],
        "dwd_listing": [
            {
                "security_id": "s1",
                "listing_code": "688001",
                "effective_from": datetime(2026, 10, 9, tzinfo=timezone.utc),
                "effective_to": None,
            }
        ],
    }
    result = domain._write_disclosures(
        tmp_path / "stage",
        source=source,
        core=core,
        products=tmp_path / "products",
        market_date=date(2026, 10, 12),
        identities=ids,
        observed=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )
    assert result["held_count"] == 1 and result["held_disclosures"][0]["ann_id"] == "prelisting"
    assert result["row_counts"]["dwd_disclosure"] == 1
