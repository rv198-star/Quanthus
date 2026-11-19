"""P12 desktop through HTTP and CLI: same use cases, same contract, trusted account binding."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from quanthus.platform.composition import create_platform_kernel
from quanthus.platform.p12 import cli, transport
from quanthus.platform.p12.assembly import build_synthetic_runtime
from quanthus.platform.p12.contract_generated import OPERATIONS, REASON_TEXTS, SCHEMA_VERSION
from quanthus.platform.p12.validation import ContractViolation, validate
from quanthus.platform.persistence import InMemoryPlatformStore

from .conftest import TestCipher, login, wsgi_request

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "P12-desktop-password-123"
A = "/api/p12/v1/accounts/SYN-P12-A"
B = "/api/p12/v1/accounts/SYN-P12-B"
FILL = {
    "schema_version": SCHEMA_VERSION,
    "report_id": "fill-1",
    "quantity": 400,
    "price": "12.50",
    "confirmed_fee": "5.00",
    "occurred_at": "2026-11-19T09:31:00+08:00",
}
PLAN = "plan-OPP-SYN-600000-2026-11-18"


def _kernel(tmp_path: Path, attribution_release: Path, *, with_runtime: bool = True):
    runtime = (
        build_synthetic_runtime(
            state_dir=tmp_path,
            account_users={
                "SYN-P12-A": frozenset({"owner", "watcher"}),
                "SYN-P12-B": frozenset({"other"}),
            },
        )
        if with_runtime
        else None
    )
    kernel = create_platform_kernel(
        store=InMemoryPlatformStore(),
        cipher=TestCipher(),
        attribution_root=attribution_release.parent,
        p12_runtime=runtime,
    )
    kernel.migrate()
    for name, role in (("owner", "operator"), ("watcher", "viewer"), ("other", "operator")):
        kernel.identity.create_user(username=name, password=PASSWORD, role=role)
    return kernel


def _auth(kernel, name: str = "owner") -> dict[str, str]:
    return {"Authorization": f"Bearer {login(kernel, username=name, password=PASSWORD)}"}


@pytest.fixture()
def p12_kernel(tmp_path: Path, attribution_release: Path):
    return _kernel(tmp_path / "http", attribution_release)


def test_manifest_routes_match_the_contract_operations(p12_kernel) -> None:
    status, _, document = wsgi_request(p12_kernel, "GET", "/openapi.json")
    assert status == 200
    exposed = {
        op["operationId"]: (method.upper(), path)
        for path, item in document["paths"].items()
        for method, op in item.items()
        if op["operationId"].startswith("p12.")
    }
    expected = {key: (value["method"], value["path"]) for key, value in OPERATIONS.items()}
    assert exposed == expected


def test_http_journey_matches_the_brief_amounts(p12_kernel) -> None:
    headers = _auth(p12_kernel)
    status, _, accounts = wsgi_request(p12_kernel, "GET", "/api/p12/v1/accounts", headers=headers)
    assert status == 200 and [a["account_id"] for a in accounts["accounts"]] == ["SYN-P12-A"]
    status, _, board = wsgi_request(p12_kernel, "GET", f"{A}/workbench", headers=headers)
    assert status == 200 and board["schema_version"] == SCHEMA_VERSION
    plan = board["package"]["plans"][0]
    assert (plan["plan_id"], plan["target_qty"], plan["price_cap"]) == (PLAN, 400, "12.40")
    body = {**FILL, "plan_id": PLAN}
    status, _, posted = wsgi_request(
        p12_kernel, "POST", f"{A}/execution-reports", payload=body, headers=headers
    )
    assert status == 200, posted
    assert (posted["status"], posted["deviations"]) == ("POSTED", ["PRICE_DEVIATION"])
    assert posted["account"]["cash_total"] == "4995.00"
    status, _, retry = wsgi_request(
        p12_kernel, "POST", f"{A}/execution-reports", payload=body, headers=headers
    )
    assert (
        retry["receipt_id"] == posted["receipt_id"] and retry["account"]["cash_total"] == "4995.00"
    )
    correction = {
        "schema_version": SCHEMA_VERSION,
        "correction_id": "corr-1",
        "fact_id": "fill-1",
        "expected_fact_revision": 1,
        "quantity": 300,
        "price": "12.50",
        "reason": "broker statement",
    }
    status, _, corrected = wsgi_request(
        p12_kernel, "POST", f"{A}/fact-corrections", payload=correction, headers=headers
    )
    assert status == 200 and corrected["cash_delta"] == "1250.00"
    assert corrected["account"]["cash_total"] == "6245.00"
    _, _, records = wsgi_request(p12_kernel, "GET", f"{A}/records", headers=headers)
    assert records["reports"][0]["quantity"] == 400
    assert records["facts"][0]["quantity"] == 300
    assert [e["cash_delta"] for e in records["events"]] == ["-5005.00", "1250.00"]
    _, _, board = wsgi_request(p12_kernel, "GET", f"{A}/workbench", headers=headers)
    assert board["account"]["cash_total"] == "6245.00"
    assert board["opportunities"][0]["status_code"] == "NEEDS_REASSESSMENT"
    _, _, rules = wsgi_request(p12_kernel, "GET", f"{A}/funding-rules", headers=headers)
    assert rules["funding"]["status_reason"] == "FUNDING_CYCLE_NOT_BOUND"
    assert rules["funding"]["reserve"] == {"value": None, "reason_code": "FUNDING_CYCLE_NOT_BOUND"}
    assert rules["retained_bucket_mode_available"] is False


def test_trusted_binding_rejects_cross_account_mode_and_role(p12_kernel) -> None:
    owner = _auth(p12_kernel)
    status, _, error = wsgi_request(p12_kernel, "GET", f"{B}/workbench", headers=owner)
    assert status == 403 and error["error"]["reason_code"] == "ACCOUNT_NOT_ADMITTED"
    status, _, error = wsgi_request(p12_kernel, "GET", f"{A}/workbench?mode=real", headers=owner)
    assert status == 403 and error["error"]["reason_code"] == "UNAUTHORIZED_CONTEXT"
    status, _, _ = wsgi_request(p12_kernel, "GET", f"{A}/workbench")
    assert status == 401
    watcher = _auth(p12_kernel, "watcher")
    status, _, _ = wsgi_request(p12_kernel, "GET", f"{A}/workbench", headers=watcher)
    assert status == 200
    status, _, error = wsgi_request(
        p12_kernel,
        "POST",
        f"{A}/execution-reports",
        payload={**FILL, "plan_id": PLAN},
        headers=watcher,
    )
    assert status == 403 and error["error"]["reason"] == "permission_denied"
    other = _auth(p12_kernel, "other")
    status, _, error = wsgi_request(
        p12_kernel,
        "POST",
        f"{A}/execution-reports",
        payload={**FILL, "plan_id": PLAN},
        headers=other,
    )
    assert status == 403 and error["error"]["reason_code"] == "ACCOUNT_NOT_ADMITTED"
    _, _, board = wsgi_request(p12_kernel, "GET", f"{A}/workbench", headers=owner)
    assert board["account"]["account_revision"] == 0


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"price": "12.5"}, 422, "INVALID_REPORT_SHAPE"),
        ({"price": 12.5}, 422, "INVALID_REPORT_SHAPE"),
        ({"extra": True}, 422, "INVALID_REPORT_SHAPE"),
        ({"schema_version": "p12.desktop.v0"}, 422, "SCHEMA_VERSION_MISMATCH"),
        ({"occurred_at": "2026-11-19T09:31:00"}, 422, "INVALID_REPORT_SHAPE"),
        ({"occurred_at": "2026-11-20T09:31:00+08:00"}, 422, "INVALID_REPORT_SHAPE"),
        ({"plan_id": "plan-missing"}, 404, "PLAN_NOT_FOUND"),
    ],
)
def test_invalid_reports_are_named_and_leave_the_account_unchanged(
    p12_kernel, change: dict[str, Any], status: int, code: str
) -> None:
    headers = _auth(p12_kernel)
    body = {**FILL, "plan_id": PLAN, **change}
    observed, _, error = wsgi_request(
        p12_kernel, "POST", f"{A}/execution-reports", payload=body, headers=headers
    )
    assert (observed, error["error"]["reason_code"]) == (status, code)
    validate("Error", error)
    _, _, board = wsgi_request(p12_kernel, "GET", f"{A}/workbench", headers=headers)
    assert board["account"]["cash_total"] == "10000.00"


def test_production_kernel_without_runtime_names_the_gap(tmp_path, attribution_release) -> None:
    kernel = _kernel(tmp_path / "none", attribution_release, with_runtime=False)
    status, _, error = wsgi_request(kernel, "GET", "/api/p12/v1/accounts", headers=_auth(kernel))
    assert status == 503 and error["error"]["reason_code"] == "P12_RUNTIME_NOT_CONFIGURED"


def _normalise(outcome: dict[str, Any]) -> dict[str, Any]:
    account = outcome["account"]
    return {
        "status": outcome["status"],
        "deviations": outcome["deviations"],
        "cash_delta": outcome["cash_delta"],
        "cash": account["cash_total"],
        "equity": account["economic_equity"],
        "positions": [(p["security_code"], p["quantity"]) for p in account["positions"]],
        "revision": account["account_revision"],
        "ledger_digest": account["ledger_digest"],
    }


def test_http_cli_and_direct_calls_produce_the_same_results(tmp_path, attribution_release) -> None:
    correction = {
        "schema_version": SCHEMA_VERSION,
        "correction_id": "corr-1",
        "fact_id": "fill-1",
        "expected_fact_revision": 1,
        "quantity": 300,
        "price": "12.50",
        "reason": "broker statement",
    }
    fill = {**FILL, "plan_id": PLAN}
    kernel = _kernel(tmp_path / "http", attribution_release)
    headers = _auth(kernel)
    http = [
        wsgi_request(kernel, "POST", f"{A}/execution-reports", payload=fill, headers=headers)[2],
        wsgi_request(kernel, "POST", f"{A}/fact-corrections", payload=correction, headers=headers)[
            2
        ],
    ]
    cli_state = tmp_path / "cli"
    cli_out = [
        cli.run("submit-fill", state_dir=cli_state, account_id="SYN-P12-A", request=fill)[1],
        cli.run("correct-fill", state_dir=cli_state, account_id="SYN-P12-A", request=correction)[1],
    ]
    runtime = build_synthetic_runtime(state_dir=tmp_path / "direct", account_users={})
    app = runtime.bindings["SYN-P12-A"].application
    direct = [
        transport.outcome_dto(app.submit_fill(app.scope, transport.parse_submit_fill(fill))),
        transport.outcome_dto(
            app.correct_fill(app.scope, transport.parse_correct_fill(correction))
        ),
    ]
    assert [_normalise(x) for x in http] == [_normalise(x) for x in cli_out]
    assert [_normalise(x) for x in http] == [_normalise(x) for x in direct]
    board_http = wsgi_request(kernel, "GET", f"{A}/workbench", headers=headers)[2]
    board_cli = cli.run("workbench", state_dir=cli_state, account_id="SYN-P12-A")[1]
    board_direct = transport.workbench_dto(app.workbench(app.scope))
    assert board_http == board_cli == board_direct


def test_cli_entry_point_prints_the_contract_dto(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "quanthus.platform.p12.cli",
            "--state-dir",
            str(tmp_path),
            "workbench",
        ],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    validate("Workbench", json.loads(result.stdout))


def test_generated_artifacts_have_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/p12_contract_codegen.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_unknown_values_and_decimal_text_round_trip() -> None:
    validate("MaybeMoney", {"value": None, "reason_code": "VALUATION_UNAVAILABLE"})
    validate("MaybeMoney", {"value": "0.00", "reason_code": None})
    for bad in (
        {"value": "0", "reason_code": None},
        {"value": 0.0, "reason_code": None},
        {"value": None, "reason_code": "NOT_A_CODE"},
        {"value": "1.00"},
    ):
        with pytest.raises(ContractViolation):
            validate("MaybeMoney", bad)


def test_fixture_reason_codes_belong_to_the_contract_catalog() -> None:
    raw = json.loads((ROOT / "config/p12/synthetic-desktop.v1.json").read_text(encoding="utf-8"))
    codes = {code for item in raw["decision_set"]["decisions"] for code in item["reason_codes"]}
    assert codes <= set(REASON_TEXTS)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_validator_accepts_python_dtos(tmp_path, attribution_release) -> None:
    kernel = _kernel(tmp_path / "js", attribution_release)
    headers = _auth(kernel)
    wsgi_request(
        kernel, "POST", f"{A}/execution-reports", payload={**FILL, "plan_id": PLAN}, headers=headers
    )
    samples = {
        "Workbench": wsgi_request(kernel, "GET", f"{A}/workbench", headers=headers)[2],
        "Records": wsgi_request(kernel, "GET", f"{A}/records", headers=headers)[2],
        "FundingRules": wsgi_request(kernel, "GET", f"{A}/funding-rules", headers=headers)[2],
    }
    sample_path = tmp_path / "samples.json"
    sample_path.write_text(json.dumps(samples), encoding="utf-8")
    script = (
        "import { readFileSync } from 'node:fs';"
        f"import {{ validate }} from '{(ROOT / 'product-web/src/p12/validate.js').as_uri()}';"
        f"const samples = JSON.parse(readFileSync('{sample_path}', 'utf8'));"
        "for (const [name, value] of Object.entries(samples)) validate(name, value);"
        "let rejected = false;"
        "try { validate('Workbench', { ...samples.Workbench, extra: 1 }) }"
        " catch { rejected = true }"
        "if (!rejected) throw new Error('extra field accepted');"
        "console.log('ok')"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
