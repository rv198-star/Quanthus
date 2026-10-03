from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quanthus.expert_evaluation.serialization import digest_value
from quanthus.expert_strategy import p3_temporary_daily_adapter as adapter
from tests.unit.test_p3_daily_adapter_probe import package as source_package

package = source_package


@pytest.fixture
def raw():
    return {
        "scope": {"rows_per_package": 2},
        "policy_version": "synthetic-temporary-adapter-v1",
        "contract": {
            "canonical_price_basis": "UNADJUSTED",
            "unavailable_reason": "P3_ECONOMIC_AVAILABILITY_UNQUALIFIED",
        },
    }


def test_serialized_values_types_and_times_remain_exact_even_when_late(
    package, raw, tmp_path, monkeypatch
):
    c, seal = package
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "adapted'private.parquet"
    row = adapter.adapt_package(c, seal(), output, raw)
    assert row["rows"] == 2
    assert row["source_differences"] == 0
    assert row["raw_known_after_close_rows"] == 1
    assert row["zero_activity_rows"] == 1
    observed = c.execute(
        "SELECT price_basis,source_price_basis,decision_admissible,unavailable_reason "
        "FROM read_parquet(?)",
        [str(output)],
    ).fetchall()
    assert observed == [("UNADJUSTED", "none", False, "P3_ECONOMIC_AVAILABILITY_UNQUALIFIED")] * 2
    schemas = {
        r[0]: r[1]
        for r in c.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(output)]).fetchall()
    }
    assert schemas["close"] == "DECIMAL(38,10)"
    assert schemas["knowledge_from"] == "TIMESTAMP WITH TIME ZONE"
    assert output.exists() and not (tmp_path / "UNADJUSTED").exists()
    assert not c.execute(
        "SELECT * FROM duckdb_tables() WHERE table_name LIKE 'adapter_%'"
    ).fetchall()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("UPDATE bars SET price_basis='qfq' WHERE interval_size='1d'", "IDENTITY_BASIS_OR_TIME"),
        ("UPDATE bars SET knowledge_from=NULL WHERE interval_size='1d'", "IDENTITY_BASIS_OR_TIME"),
        ("UPDATE calendar SET trade_date=DATE '2026-08-04'", "IDENTITY_BASIS_OR_TIME"),
        ("UPDATE bars SET high=0 WHERE interval_size='1d'", "PRICE_INVALID"),
        (
            "INSERT INTO bars SELECT * FROM bars WHERE interval_size='1d' LIMIT 1",
            "IDENTITY_DUPLICATE",
        ),
        ("ALTER TABLE bars ADD COLUMN decision_admissible BOOLEAN", "ALREADY_ADAPTED_SOURCE"),
    ],
)
def test_bad_inputs_never_publish_a_corrected_file(package, raw, tmp_path, mutation, reason):
    c, seal = package
    c.execute(mutation)
    output = tmp_path / "rejected.parquet"
    with pytest.raises(ValueError, match=reason):
        adapter.adapt_package(c, seal(), output, raw)
    assert not output.exists()


@pytest.mark.parametrize("budget_mode", ["normal", "tmp_spill", "output_exceeds"])
def test_complete_build_is_unavailable_and_attempt_cannot_be_reset(
    package, raw, tmp_path, monkeypatch, budget_mode
):
    _c, seal = package
    source = seal()
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    implementation = repo / adapter.IMPLEMENTATION
    implementation.parent.mkdir(parents=True)
    implementation.write_bytes(Path(adapter.__file__).read_bytes())
    files = {adapter.IMPLEMENTATION: adapter.sha256(implementation)}
    (root / "code-receipt.json").write_text(json.dumps({"code_revision": "a" * 40, "files": files}))
    raw.update(
        {
            "nodes": {"OCI": {"uid": os.getuid(), "output_root": str(root)}},
            "budgets": {
                "threads": 1,
                "memory_limit": "256MiB",
                "tmp_limit": "64MiB",
                "wall_seconds_per_node": 10,
                "output_max_bytes_per_node": 1048576,
            },
            "lifecycle": {"kind": "content_addressed_cache", "reuse": "disabled"},
        }
    )
    monkeypatch.setattr(adapter, "request", lambda *args: (raw, [source]))
    if budget_mode == "tmp_spill":
        original = adapter.adapt_package

        def simulated_spill(c, package, output, raw):
            result = original(c, package, output, raw)
            temp = next(output.parent.glob("adapter-tmp-*"))
            (temp / "spill.tmp").write_bytes(b"0" * 1048577)
            return result

        monkeypatch.setattr(adapter, "adapt_package", simulated_spill)
    if budget_mode == "output_exceeds":
        raw["budgets"]["output_max_bytes_per_node"] = 1
        with pytest.raises(adapter.ProbeBudgetExceeded, match="OUTPUT_BUDGET"):
            adapter.build(repo, "OCI", "a" * 40)
        assert (root / "attempt.json").exists()
        assert not list(root.glob("*/receipt.json"))
        with pytest.raises(FileExistsError):
            adapter.build(repo, "OCI", "a" * 40)
        return
    result = adapter.build(repo, "OCI", "a" * 40)
    assert result["disposition"] == "TEMPORARY_BASIS_ADAPTED_PIT_UNQUALIFIED"
    assert result["decision_admissible_rows"] == 0
    assert result["B_accepted"] is result["D_activated"] is result["raw_times_changed"] is False
    assert result["rows"] == 2 and result["stages"][1]["tmp_removed"]
    if budget_mode == "tmp_spill":
        assert (
            result["stages"][1]["tmp_bytes_at_close"] > raw["budgets"]["output_max_bytes_per_node"]
        )
    digest = result.pop("result_digest")
    assert digest == digest_value(result)
    output = Path(result["output_root"])
    assert output.name == result["cache_digest"].removeprefix("sha256:")
    before = (output / "receipt.json").read_bytes()
    with pytest.raises(FileExistsError):
        adapter.build(repo, "OCI", "a" * 40)
    assert (output / "receipt.json").read_bytes() == before


def test_wrong_uid_fails_before_any_materialization(tmp_path):
    raw = {"nodes": {"OCI": {"uid": os.getuid() + 1, "output_root": str(tmp_path / "private")}}}
    with pytest.raises(ValueError, match="PRIVATE_ROOT"):
        adapter.private_root(raw, "OCI")
    assert not (tmp_path / "private").exists()


def test_policy_and_successful_receipt_bindings_are_not_invented():
    repo = Path(__file__).parents[2]
    assert adapter.sha256(repo / adapter.CONFIG) == adapter.CONFIG_SHA
    raw = json.loads((repo / adapter.CONFIG).read_bytes())
    for binding in ("source_request", "source_access_recovery"):
        assert adapter.sha256(repo / raw[binding]["path"]) == raw[binding]["sha256"]
    assert raw["scope"]["packages"] == 35
    assert raw["contract"]["decision_admissible"] is False
    assert raw["authority"]["retained_B_attempt_reopened"] is False


@pytest.fixture
def bound_request(package, tmp_path, monkeypatch):
    _c, seal = package
    p = {**seal(), "node": "OCI"}
    repo = tmp_path / "source"
    (repo / "config").mkdir(parents=True)
    research = repo / "config/research.json"
    research.write_text('{"synthetic":true}')
    source = repo / "config/source.json"
    source.write_text(
        json.dumps(
            {
                "packages": [p],
                "authority": {
                    "original_research_request": {
                        "path": "config/research.json",
                        "sha256": adapter.sha256(research),
                    }
                },
            }
        )
    )
    recovery = repo / "config/recovery.json"
    recovery.write_text(
        json.dumps(
            {
                "root_relocation": {
                    "market_date": p["market_date"],
                    "old_root": p["root"],
                    "new_root": p["root"],
                }
            }
        )
    )
    receipt = {
        "node": "OCI",
        "disposition": "INPUT_ONLY_ADAPTER_EVIDENCE",
        "daily": [{"market_date": p["market_date"], "raw_1d_rows": 2}],
        "aggregate": {"unknown_basis_rows": 0, "invalid_price_rows": 0},
        "B_accepted": False,
        "D_activated": False,
        "P6_or_formal_data_mutated": False,
    }
    receipt["result_digest"] = digest_value(receipt)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    config = {
        "source_request": {"path": "config/source.json", "sha256": adapter.sha256(source)},
        "source_access_recovery": {
            "path": "config/recovery.json",
            "sha256": adapter.sha256(recovery),
        },
        "scope": {
            "packages": 1,
            "payload_files": 2,
            "payload_bytes": sum(f["size_bytes"] for f in p["files"]),
            "start": p["market_date"],
            "end": p["market_date"],
            "rows_per_package": 2,
        },
        "nodes": {
            "OCI": {
                "diagnostic_receipt": str(receipt_path),
                "diagnostic_receipt_sha256": adapter.sha256(receipt_path),
                "diagnostic_result_digest": receipt["result_digest"],
            }
        },
    }
    config_path = repo / adapter.CONFIG
    config_path.parent.mkdir(parents=True)

    def reseal():
        config_path.write_text(json.dumps(config))
        monkeypatch.setattr(adapter, "CONFIG_SHA", adapter.sha256(config_path))

    reseal()
    return repo, config, reseal, receipt_path


def test_bound_request_verifies_metadata_without_importing_market_rows(bound_request):
    repo, config, _seal, _receipt = bound_request
    raw, packages = adapter.request(repo, "OCI")
    assert raw == config and len(packages) == 1


def test_changed_diagnostic_receipt_cannot_authorize_an_adapter(bound_request):
    repo, _config, _seal, receipt = bound_request
    receipt.write_text("{}")
    with pytest.raises(ValueError, match="DIAGNOSTIC_DRIFT"):
        adapter.request(repo, "OCI")


def test_a_resealed_b_acceptance_claim_does_not_qualify_input(bound_request):
    repo, config, reseal, receipt_path = bound_request
    receipt = json.loads(receipt_path.read_text())
    receipt.pop("result_digest")
    receipt["B_accepted"] = True
    receipt["result_digest"] = digest_value(receipt)
    receipt_path.write_text(json.dumps(receipt))
    config["nodes"]["OCI"].update(
        diagnostic_receipt_sha256=adapter.sha256(receipt_path),
        diagnostic_result_digest=receipt["result_digest"],
    )
    reseal()
    with pytest.raises(ValueError, match="DIAGNOSTIC_UNQUALIFIED"):
        adapter.request(repo, "OCI")


def test_an_unlisted_date_cannot_enter_the_temporary_scope(bound_request):
    repo, config, reseal, _receipt = bound_request
    config["scope"]["start"] = "2026-08-04"
    reseal()
    with pytest.raises(ValueError, match="SCOPE_DRIFT"):
        adapter.request(repo, "OCI")
