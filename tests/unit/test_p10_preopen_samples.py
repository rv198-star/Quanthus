import json
from datetime import timedelta
from urllib.error import HTTPError

import pytest

from quanthus.p10_preopen_samples import (
    SamplingError,
    capture_slot,
    freeze_pool,
    instant,
    persist_pool,
    raw_fields,
    run_pool,
    sha,
    verify_pool,
)


def pool_input():
    return {
        "sample_date": "2026-11-25",
        "reference_trade_date": "2026-11-24",
        "fallback_sha256": "4131dac0b0462203391826e5fb1c259c42218746668224981ed46fec7216d56c",
        "securities": [
            {
                "security_code": code,
                "tencent_symbol": symbol,
                "mic_code": {"sh": "XSHG", "sz": "XSHE"}[symbol[:2]],
                "reference_evidence": [
                    {
                        "uri": "fixture:T-reference",
                        "sha256": "a" * 64,
                        "observed_at": "2026-11-24T18:30:00+08:00",
                    }
                ],
            }
            for code, symbol in [("002517", "sz002517"), ("600000", "sh600000")]
        ],
        "coverage_gaps": ["SYNTHETIC_FIXTURE_ONLY"],
    }


def frozen():
    return freeze_pool(pool_input(), instant("2026-11-24T20:00:00+08:00"))


def wire():
    rows = []
    for symbol in ["sh600000", "sz002517"]:
        fields = [""] * 65
        fields[2], fields[3], fields[5], fields[6] = symbol[2:], "10", "9", "0"
        fields[30] = "20261125091505"
        rows.append("v_" + symbol + '="' + "~".join(fields) + '";')
    return "\n".join(rows).encode()


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        return wire()


def test_freeze_and_verify_round_trip_and_tamper():
    p = frozen()
    verify_pool(p)
    p["securities"][0]["tencent_symbol"] = "sh002517"
    with pytest.raises(SamplingError, match="FROZEN_POOL_SHA_MISMATCH"):
        verify_pool(p)


@pytest.mark.parametrize("stamp", ["2026-11-25T09:15:00+08:00", "2026-11-25T12:00:00+08:00"])
def test_pool_cannot_be_frozen_after_cutoff(stamp):
    with pytest.raises(SamplingError, match="POOL_FREEZE_TOO_LATE"):
        freeze_pool(pool_input(), instant(stamp))


@pytest.mark.parametrize("change", ["wrong_T", "future_reference", "missing_holding", "forbidden"])
def test_invalid_pool_inputs_fail_closed(change):
    p = pool_input()
    if change == "wrong_T":
        p["reference_trade_date"] = "2026-11-23"
    elif change == "future_reference":
        p["securities"][0]["reference_evidence"][0]["observed_at"] = "2026-11-26T01:00:00Z"
    elif change == "missing_holding":
        p["securities"][0].update(security_code="000001", tencent_symbol="sz000001")
    else:
        p["securities"][0].update(security_code="300308", tencent_symbol="sz300308")
    with pytest.raises(SamplingError):
        freeze_pool(p, instant("2026-11-24T20:00:00+08:00"))


def test_all_raw_fields_preserved_without_invented_auction_or_halt():
    rows = raw_fields(wire(), ["sh600000", "sz002517"])
    assert all(len(r["fields"]) == 65 for r in rows)
    assert all(r["fields"][6] == "0" for r in rows)
    assert all(r["auction_field"] is None and r["suspension_field"] is None for r in rows)
    assert rows[0]["quote_time_raw"] == "20261125091505"


def test_capture_reserves_before_transport_and_rejects_exact_retry(tmp_path):
    calls = []
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))

    def opener(req, timeout):
        assert (tmp_path / "091505.request.json").exists()
        assert req.full_url == "https://qt.gtimg.cn/q=sh600000,sz002517"
        calls.append(req.full_url)
        return Response()

    clock = lambda: instant("2026-11-25T09:15:05+08:00")  # noqa: E731
    receipt = capture_slot(frozen(), tmp_path, "09:15:05", clock=clock, opener=opener)
    assert receipt["status"] == "CAPTURED"
    assert receipt["raw_sha256"] == sha((tmp_path / "091505.wire").read_bytes())
    with pytest.raises(SamplingError, match="SLOT_ALREADY_RESERVED_NO_RETRY"):
        capture_slot(frozen(), tmp_path, "09:15:05", clock=clock, opener=opener)
    assert len(calls) == 1


def test_missed_slot_never_calls_transport(tmp_path):
    def bad_opener(*args, **kwargs):
        pytest.fail("must not make a late source request")

    with pytest.raises(SamplingError, match="SLOT_NOT_CURRENT"):
        capture_slot(
            frozen(),
            tmp_path,
            "09:15:05",
            clock=lambda: instant("2026-11-25T09:16:00+08:00"),
            opener=bad_opener,
        )


def test_429_stops_remaining_slots_without_retry(tmp_path):
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))

    def opener(req, timeout):
        raise HTTPError(req.full_url, 429, "held", {}, None)

    receipt = capture_slot(
        frozen(),
        tmp_path,
        "09:15:05",
        clock=lambda: instant("2026-11-25T09:15:05+08:00"),
        opener=opener,
    )
    assert receipt["status"] == "unavailable"
    assert (tmp_path / "HTTP-STOP.json").exists()
    with pytest.raises(SamplingError, match="SOURCE_HELD_NO_MORE_REQUESTS"):
        capture_slot(
            frozen(),
            tmp_path,
            "09:20:05",
            clock=lambda: instant("2026-11-25T09:20:05+08:00"),
            opener=opener,
        )


def test_late_receipt_stays_unqualified(tmp_path):
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))
    ticks = iter([instant("2026-11-25T09:29:50+08:00"), instant("2026-11-25T09:30:01+08:00")])
    result = capture_slot(
        frozen(), tmp_path, "09:29:50", clock=lambda: next(ticks), opener=lambda *a, **k: Response()
    )
    assert result["status"] == "LATE_RECEIPT_UNQUALIFIED"


def test_unknown_source_clock_remains_null():
    body = wire().replace(b"20261125091505", b"bad-clock")
    assert all(r["quote_time"] is None for r in raw_fields(body, ["sh600000", "sz002517"]))


def test_dedicated_root_never_writes_beside_other_data(tmp_path):
    (tmp_path / "unrelated").write_text("preserve")
    with pytest.raises(SamplingError, match="DEDICATED_EVIDENCE_ROOT_REQUIRED"):
        persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))
    assert (tmp_path / "unrelated").read_text() == "preserve"


def test_pool_and_freeze_receipt_are_durable_before_waiting(tmp_path):
    tick = instant("2026-11-25T08:59:00+08:00")
    seen = []

    def sleep(seconds):
        nonlocal tick
        assert (tmp_path / "POOL.json").exists()
        receipt = json.loads((tmp_path / "FREEZE.json").read_bytes())
        assert instant(receipt["recorded_at"]) < instant("2026-11-25T09:15:00+08:00")
        tick += timedelta(seconds=seconds)

    def opener(*args, **kwargs):
        seen.append(tick)
        return Response()

    run_pool(frozen(), tmp_path, clock=lambda: tick, sleeper=sleep, opener=opener)
    assert len(seen) == 5
    assert len(list(tmp_path.glob("*.request.json"))) == 5


def test_late_first_persistence_makes_no_request(tmp_path):
    with pytest.raises(SamplingError, match="DURABLE_FREEZE_TOO_LATE"):
        capture_slot(
            frozen(),
            tmp_path,
            "09:15:05",
            clock=lambda: instant("2026-11-25T09:15:05+08:00"),
            opener=lambda *a, **k: pytest.fail("no request"),
        )


def test_crash_after_reservation_cannot_retry(tmp_path):
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))
    (tmp_path / "091505.request.json").write_text("reserved before process crash")
    with pytest.raises(SamplingError, match="SLOT_ALREADY_RESERVED_NO_RETRY"):
        capture_slot(
            frozen(), tmp_path, "09:15:05", clock=lambda: instant("2026-11-25T09:15:05+08:00")
        )


def test_orphan_pool_may_be_completed_only_before_cutoff(tmp_path):
    from quanthus.p10_preopen_samples import encoded

    (tmp_path / "POOL.json").write_bytes(encoded(frozen()))
    with pytest.raises(SamplingError, match="DURABLE_FREEZE_TOO_LATE"):
        persist_pool(frozen(), tmp_path, instant("2026-11-25T09:15:00+08:00"))
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))
    assert (tmp_path / "FREEZE.json").exists()


def test_parse_failure_still_records_raw_identity(tmp_path):
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))

    class BadResponse(Response):
        def read(self, limit):
            return wire().replace(b"600000", b"600001", 1)

    receipt = capture_slot(
        frozen(),
        tmp_path,
        "09:15:05",
        clock=lambda: instant("2026-11-25T09:15:05+08:00"),
        opener=lambda *a, **k: BadResponse(),
    )
    assert receipt["status"] == "unavailable"
    assert receipt["raw_sha256"] == sha((tmp_path / "091505.wire").read_bytes())


def test_redirect_is_rejected_without_followup():
    from quanthus.p10_preopen_samples import NoRedirect

    with pytest.raises(SamplingError, match="SOURCE_REDIRECT_REJECTED"):
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://another.example/")


def test_clock_regression_retains_wire_with_unavailable_receipt(tmp_path):
    persist_pool(frozen(), tmp_path, instant("2026-11-25T09:00:00+08:00"))
    ticks = iter([instant("2026-11-25T09:15:05+08:00"), instant("2026-11-25T09:15:04+08:00")])
    receipt = capture_slot(
        frozen(), tmp_path, "09:15:05", clock=lambda: next(ticks), opener=lambda *a, **k: Response()
    )
    assert receipt["status"] == "unavailable"
    assert "RECEIPT_CLOCK_REGRESSION" in receipt["reason"]
    assert receipt["raw_sha256"]
