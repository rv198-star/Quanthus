"""Purpose-limited futures units, native labels and expiry before network access."""

import copy
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest

from quanthus import p10_supplement as runner
from quanthus.p10_source_contracts import normalize, policy, source_scope_failure

CONFIG = Path(__file__).resolve().parents[2] / "config/pipelines/p10-supplement-sources.v3.json"
STAMP = datetime(2001, 2, 3, tzinfo=timezone.utc)
KEY = "sina:futures.price.snapshot"


@pytest.fixture
def config():
    value = copy.deepcopy(policy(CONFIG))
    value["sources"][0]["contract_scope"].update(
        contract="V0103",
        delivery_month="2001-03",
        not_before=(STAMP - timedelta(days=1)).isoformat(),
        stop_before=(STAMP + timedelta(minutes=1)).isoformat(),
    )
    return value


def item(**changes):
    return Obj(
        **{
            "contract": "V0103",
            "venue": "DCE",
            "delivery_month": "2001-03",
            "name": "PVC0103",
            "last_native": Decimal("5105.25"),
            "open_native": Decimal("5101"),
            "high_native": Decimal("5120"),
            "low_native": Decimal("5090"),
            "source_date_label": date(2001, 2, 2),
            "source_time_text": "230000",
            "unqualified_fields": ((14, "123456"),),
            **changes,
        }
    )


def result(record, at=STAMP):
    return Obj(
        observation=Obj(
            request=Obj(provider="sina", capability="futures.price.snapshot"), acquired_at=at
        ),
        status=Obj(value="SUCCESS"),
        records=(record,),
        reason="SYNTHETIC_CONTRACT",
    )


def test_price_is_per_tonne_and_contract_size_is_separate(config):
    value = normalize(result(item()), config["sources"][0], config)
    row = value["records"][0]
    assert row["metrics"]["last"]["value"] == "5105.25"
    assert row["metrics"]["last"]["unit"] == "CNY_PER_TONNE"
    assert row["contract_size"]["value"] == "5"
    assert row["contract_size"]["used_as_price_multiplier"] is False
    assert row["unqualified_source_fields"] == {"14": "123456"}
    assert set(row["metrics"]) == {"last", "open", "high", "low"}
    assert value["quality_ok"] and not value["business_admission"]


@pytest.mark.parametrize(
    "label,clock",
    [
        (date(2001, 2, 2), "230000"),
        (date(2001, 2, 5), "210000"),
    ],
)
def test_native_night_labels_cannot_infer_calendar_or_trading_day(config, label, clock):
    value = normalize(
        result(item(source_date_label=label, source_time_text=clock)), config["sources"][0], config
    )
    row = value["records"][0]
    assert row["source_date_label"] == label.isoformat() and row["source_time_text"] == clock
    assert row["source_clock"] is None and row["trading_day"] is None
    assert row["quote_calendar_date"] is None and row["publication_at"] is None
    assert row["knowledge_from"] == STAMP.isoformat() and not row["real_time_admission"]
    assert "FUTURES_DATE_LABEL_ROLE_UNPROVEN" in value["quality_flags"]


def test_wrong_contract_cannot_reuse_qualified_units(config):
    with pytest.raises(ValueError, match="SOURCE_FUTURES_CONTRACT_MISMATCH"):
        normalize(result(item(contract="V0104")), config["sources"][0], config)


@pytest.mark.parametrize(
    "change",
    [
        {"contract": "V0"},
        {"delivery_month": "2001-04"},
        {"venue": "CZCE"},
        {"stop_before": "2001-03-01T00:00:01+08:00"},
    ],
)
def test_malformed_or_delivery_month_scope_fails_closed(config, change):
    spec = config["sources"][0]
    spec["contract_scope"].update(change)
    assert source_scope_failure(spec, STAMP) == "FUTURES_CONTRACT_SCOPE_INVALID"


def test_before_qualification_and_at_exact_expiry_are_unavailable(config):
    spec = config["sources"][0]
    assert (
        source_scope_failure(spec, STAMP - timedelta(days=2))
        == "FUTURES_QUALIFICATION_NOT_YET_VALID"
    )
    assert source_scope_failure(spec, STAMP) is None
    assert (
        source_scope_failure(spec, STAMP + timedelta(minutes=1))
        == "FUTURES_CONTRACT_QUALIFICATION_EXPIRED"
    )


def test_expiry_on_same_utc_day_bypasses_cache_and_does_not_fetch(config, tmp_path, monkeypatch):
    astock = pytest.importorskip("astock")
    state = {"at": STAMP, "calls": 0}
    request = astock.SourceRequest(
        "sina", "futures.price.snapshot", "GET", "https://example.invalid/quote"
    )

    def parse(raw):
        return astock.FetchResult(
            raw,
            astock.Status.FAILED if raw.error else astock.Status.SUCCESS,
            raw.error or "SYNTHETIC",
            () if raw.error else (item(),),
        )

    monkeypatch.setattr(runner, "library_contracts", lambda _: {KEY: (request, parse)})

    def fetch(req, _):
        state["calls"] += 1
        return astock.RawObservation(req, state["at"], b"synthetic", 200)

    root = tmp_path / "futures"
    first = runner.cycle(root, config, "a" * 40, fetcher=fetch, clock=lambda: state["at"])
    assert first["network_requests"] == 1
    state["at"] += timedelta(minutes=1)
    expired = runner.cycle(root, config, "a" * 40, fetcher=fetch, clock=lambda: state["at"])
    assert expired["run_id"] != first["run_id"] and expired["network_requests"] == 0
    assert state["calls"] == 1
    ledger = json.loads((root / "candidates.json").read_bytes())["candidates"][0]
    assert ledger["status"] == "HOLD_CONTRACT_SCOPE"
    path = root / "runs" / expired["run_id"]
    manifest = runner.verified_run(path)
    value = json.loads((path / manifest["sources"][KEY]).read_bytes())
    assert value["previous_good_run"] == first["run_id"] and not value["previous_good_reused"]
    with pytest.raises(ValueError, match="CURRENT_OBSERVATION_UNAVAILABLE"):
        runner.read_observation(
            root,
            expired["run_id"],
            KEY,
            state["at"].isoformat(),
            policy_sha256=runner.digest(config),
        )
