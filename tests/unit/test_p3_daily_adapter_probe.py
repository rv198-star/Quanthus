from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest

from quanthus.expert_strategy import p3_daily_adapter_probe as probe


@pytest.fixture
def package(tmp_path):
    c = duckdb.connect(":memory:")
    c.execute("""CREATE TABLE calendar AS SELECT 'session-a' AS calendar_session_id,
        DATE '2026-08-03' AS trade_date, true AS is_open,
        TIMESTAMPTZ '2026-08-03 07:00:00+00' AS close_at;
        CREATE TABLE bars AS SELECT * FROM (VALUES
          ('listing-a','session-a','1d','none',1::DECIMAL(38,10),2::DECIMAL(38,10),
           1::DECIMAL(38,10),1.5::DECIMAL(38,10),100::DECIMAL(38,10),150::DECIMAL(38,10),
           TIMESTAMPTZ '2026-08-04 01:00:00+00'),
          ('listing-b','session-a','1d','none',1,2,1,1.5,0,0,
           TIMESTAMPTZ '2026-08-03 06:00:00+00'),
          ('listing-a','session-a','1m','none',1,2,1,1.5,10,15,
           TIMESTAMPTZ '2026-08-03 06:00:00+00'))
          b(listing_id,calendar_session_id,interval_size,price_basis,
            open,high,low,close,volume,turnover,knowledge_from)""")
    root = tmp_path / "package"
    root.mkdir()

    def seal():
        files = []
        for table, path in (
            ("bars", "payload/dwd_security_bar/part.parquet"),
            ("calendar", "payload/dwd_trading_calendar/part.parquet"),
        ):
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                p.unlink()
            c.execute(f"COPY {table} TO '{p}' (FORMAT PARQUET)")
            files.append({"path": path, "sha256": probe.sha256(p), "size_bytes": p.stat().st_size})
        raw = {
            "market_date": "2026-08-03",
            "package_id": "package-a",
            "code_revision": "a" * 40,
            "files": files,
        }
        m = root / "manifest.json"
        m.write_text(json.dumps(raw))
        (root / "READY").write_text(probe.sha256(m) + "\n")
        return {"root": str(root), "manifest_sha256": probe.sha256(m), **raw}

    yield c, seal
    c.close()


def test_real_daily_basis_is_preserved_without_inferred_pit(package):
    c, seal = package
    p = seal()
    assert probe.verify_packages([p]) == sum(f["size_bytes"] for f in p["files"])
    row = probe.probe_package(c, p)
    assert row == {
        "market_date": "2026-08-03",
        "raw_1d_rows": 2,
        "legacy_selected_rows": 0,
        "corrected_basis_rows": 2,
        "unknown_basis_rows": 0,
        "invalid_price_rows": 0,
        "zero_activity_rows": 1,
        "unknown_raw_time_rows": 0,
        "raw_known_after_close_rows": 1,
        "raw_known_by_close_rows": 1,
    }
    # The late timestamp remains the original late timestamp; the probe never projects it.
    assert str(
        c.execute(
            "SELECT knowledge_from FROM bars WHERE listing_id='listing-a' AND interval_size='1d'"
        ).fetchone()[0]
    ).startswith("2026-08-04")


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (
            "INSERT INTO bars SELECT * FROM bars WHERE interval_size='1d' "
            "AND listing_id='listing-a'",
            "ADAPTER_1D_IDENTITY_DUPLICATE",
        ),
        ("INSERT INTO calendar SELECT * FROM calendar", "ADAPTER_CALENDAR_IDENTITY_DUPLICATE"),
        ("UPDATE calendar SET trade_date=DATE '2026-08-04'", "ADAPTER_CALENDAR_SCOPE_INVALID"),
        (
            "UPDATE bars SET calendar_session_id='missing' WHERE interval_size='1d'",
            "ADAPTER_CALENDAR_SCOPE_INVALID",
        ),
        ("UPDATE calendar SET is_open=false", "ADAPTER_CALENDAR_SCOPE_INVALID"),
        ("UPDATE bars SET listing_id=NULL WHERE interval_size='1d'", "ADAPTER_1D_IDENTITY_NULL"),
    ],
)
def test_identity_and_calendar_mismatch_block_probe(package, mutation, reason):
    c, seal = package
    c.execute(mutation)
    with pytest.raises(ValueError, match=reason):
        probe.probe_package(c, seal())


def test_unknown_basis_is_counted_without_aliasing(package):
    c, seal = package
    c.execute("UPDATE bars SET price_basis='adjusted-unknown' WHERE listing_id='listing-a'")
    row = probe.probe_package(c, seal())
    assert row["unknown_basis_rows"] == 1
    assert row["corrected_basis_rows"] == 1
    assert row["legacy_selected_rows"] == 0


def test_invalid_prices_and_unknown_times_are_distinct(package):
    c, seal = package
    c.execute(
        "UPDATE bars SET \"open\"=NULL,knowledge_from=NULL WHERE listing_id='listing-a' "
        "AND interval_size='1d'"
    )
    row = probe.probe_package(c, seal())
    assert row["invalid_price_rows"] == row["unknown_raw_time_rows"] == 1
    assert row["raw_known_by_close_rows"] == 1


@pytest.mark.parametrize(
    "member", ["manifest.json", "READY", "payload/dwd_security_bar/part.parquet"]
)
def test_bytes_or_ready_drift_blocks_before_market_read(package, member):
    _, seal = package
    p = seal()
    path = Path(p["root"]) / member
    path.write_bytes(path.read_bytes() + b"bad")
    with pytest.raises(ValueError, match="ADAPTER_(MANIFEST|READY|PAYLOAD)_DRIFT"):
        probe.verify_packages([p])


def test_symlink_source_is_never_reused(package, tmp_path):
    _, seal = package
    p = seal()
    path = Path(p["root"]) / p["files"][0]["path"]
    moved = tmp_path / "outside.parquet"
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(ValueError, match="ADAPTER_SOURCE_PATH_INVALID"):
        probe.verify_packages([p])


def test_attempt_marker_cannot_be_reopened(tmp_path):
    path = tmp_path / "attempt.json"
    probe._write(path, {"maximum_attempts": 1})
    with pytest.raises(FileExistsError):
        probe._write(path, {"maximum_attempts": 1})


def test_frozen_probe_keeps_original_request_and_no_result_authority():
    root = Path(__file__).resolve().parents[2]
    assert probe.sha256(root / probe.REQUEST_CONFIG) == probe.REQUEST_SHA
    request = json.loads((root / probe.REQUEST_CONFIG).read_text())
    assert len(request["packages"]) == request["scope"]["expected_sessions"] == 35
    assert len({p["market_date"] for p in request["packages"]}) == 35
    assert sum(len(p["files"]) for p in request["packages"]) == 70
    assert sum(f["size_bytes"] for p in request["packages"] for f in p["files"]) == 846352085
    assert request["outputs"] == {
        "aggregate_counts_only": True,
        "corrected_bundle": False,
        "candidate_scan": False,
        "strategy_or_labels": False,
        "B_acceptance": False,
        "D_activation": False,
        "P6_or_formal_data_mutation": False,
    }


def test_complete_build_seals_diagnostic_and_cannot_rerun(package, tmp_path, monkeypatch):
    _, seal = package
    p = seal()
    repo = tmp_path / "repo"
    config = repo / probe.REQUEST_CONFIG
    config.parent.mkdir(parents=True)
    authority = repo / "config/authority.json"
    authority.write_text("{}")
    source = repo / "src/quanthus/expert_strategy/p3_daily_adapter_probe.py"
    source.parent.mkdir(parents=True)
    shutil.copyfile(probe.__file__, source)
    output = tmp_path / "private/attempt"
    raw = {
        "authority": {
            "original_research_request": {
                "path": "config/authority.json",
                "sha256": probe.sha256(authority),
            }
        },
        "nodes": {"OCI": {"output": str(output), "uid": os.getuid()}},
        "packages": [{**p, "node": "OCI"}],
        "budgets": {
            "wall_seconds_per_node": 30,
            "threads": 1,
            "memory_limit": "256MB",
            "tmp_limit": "16MiB",
            "output_max_bytes": 65536,
        },
    }
    config.write_text(json.dumps(raw))
    monkeypatch.setattr(probe, "REQUEST_SHA", probe.sha256(config))
    for args in (
        ("init",),
        ("add", "."),
        (
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Synthetic probe fixture",
        ),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    revision = (
        subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    )
    probe.prepare_code_receipt(repo=repo, node="OCI", code_revision=revision)
    result = probe.build(repo=repo, node="OCI", code_revision=revision)
    assert result["aggregate"]["corrected_basis_rows"] == 2
    assert result["B_accepted"] is result["economic_availability_inferred"] is False
    assert result["stages"][1]["tmp_removed"] is True
    assert not list(output.glob("adapter-tmp-*"))
    assert json.loads((output / "receipt.json").read_text()) == result
    with pytest.raises(FileExistsError):
        probe.build(repo=repo, node="OCI", code_revision=revision)


def test_pre_read_recovery_preserves_entire_payload_closure(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    original = json.loads((root / probe.REQUEST_CONFIG).read_text())
    recovery = json.loads((root / probe.RECOVERY_CONFIG).read_text())
    assert probe.sha256(root / probe.RECOVERY_CONFIG) == probe.RECOVERY_SHA
    repo = tmp_path / "repo"
    request = repo / probe.REQUEST_CONFIG
    request.parent.mkdir(parents=True)
    shutil.copyfile(root / probe.REQUEST_CONFIG, request)
    prior = tmp_path / "prior"
    prior.mkdir()
    recovery["old_attempt"]["root"] = str(prior)
    for f in recovery["old_attempt"]["files"]:
        path = prior / f["path"]
        path.write_text("Synthetic pre-row failure evidence")
        f.update(sha256=probe.sha256(path), size_bytes=path.stat().st_size)
    path = repo / probe.RECOVERY_CONFIG
    path.write_text(json.dumps(recovery))
    monkeypatch.setattr(probe, "RECOVERY_SHA", probe.sha256(path))
    recovered, active = probe._request(repo, "OCI", True)
    assert active == probe.RECOVERY_SHA
    assert recovered["scope"] == original["scope"]
    assert recovered["budgets"] == original["budgets"]
    for old, new in zip(original["packages"], recovered["packages"], strict=True):
        assert old["files"] == new["files"]
        assert {k: v for k, v in old.items() if k != "root"} == {
            k: v for k, v in new.items() if k != "root"
        }
        assert (old["root"] != new["root"]) is (old["market_date"] == "2026-08-27")
    assert recovered["nodes"]["CN01"] == original["nodes"]["CN01"]
    with pytest.raises(ValueError, match="ADAPTER_RECOVERY_NOT_ADMITTED"):
        probe._request(repo, "CN01", True)
    (prior / "attempt").mkdir()
    (prior / "attempt/receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="ADAPTER_COMPLETED_ATTEMPT_CANNOT_RECOVER"):
        probe._request(repo, "OCI", True)
