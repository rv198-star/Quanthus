from __future__ import annotations

from quanthus.platform import api as api_module
from quanthus.platform.event_worker import (
    _seed_structure_mvp_qualification_jobs,
    _write_structure_qualification_seed_receipt,
)
from quanthus.platform.identity import totp_code

from .conftest import login, wsgi_request


def test_health_openapi_request_id_and_removed_routes(kernel) -> None:
    status, headers, payload = wsgi_request(
        kernel,
        "GET",
        "/health",
        headers={"X-Request-ID": "platform-acceptance-42"},
    )
    assert status == 200
    assert payload["status"] == "healthy"
    assert headers["X-Request-ID"] == "platform-acceptance-42"

    status, _, document = wsgi_request(kernel, "GET", "/openapi.json")
    assert status == 200
    rendered = str(document).casefold()
    assert "/api/platform/v1/quanthus/gates" in document["paths"]
    assert "/api/platform/v1/product/catalog" in document["paths"]
    assert "/api/platform/v1/product/candidates/{strategy_id}" in document["paths"]
    assert "/api/platform/v1/product/candidates/{strategy_id}/reviews" in document["paths"]
    assert "/api/platform/v1/product/candidates/{strategy_id}/governance" in document["paths"]
    assert "/api/platform/v1/jobs/{job_id}" in document["paths"]
    assert all("<job_id>" not in path for path in document["paths"])
    assert all(
        fragment not in rendered
        for fragment in (
            "/mcp",
            "oauth",
            "billing",
            "credits",
            "broker",
            "fast-analysis",
            "ai-chat",
        )
    )
    assert wsgi_request(kernel, "GET", "/api/mcp/tools")[0] == 404


def test_health_can_project_bounded_structure_mvp_qualification(
    kernel, tmp_path, monkeypatch
) -> None:
    revision = "f" * 40
    job_ids = _seed_structure_mvp_qualification_jobs(
        kernel.store,
        enabled_value="1",
        code_revision=revision,
    )
    receipt = tmp_path / "structure-qualification-seed.json"
    _write_structure_qualification_seed_receipt(
        tmp_path,
        code_revision=revision,
        status="submitted",
        job_ids=job_ids,
    )
    monkeypatch.setattr(api_module, "_STRUCTURE_MVP_SEED_RECEIPT", receipt)
    monkeypatch.setenv("QUANTHUS_STRUCTURE_MVP_HEALTH_PROBE", "1")

    status, _, payload = wsgi_request(kernel, "GET", "/health")

    assert status == 200
    qualification = payload["structure_mvp_qualification"]
    assert qualification["status"] == "running"
    assert qualification["code_revision"] == revision
    assert [item["job_id"] for item in qualification["runs"]] == list(job_ids)
    assert {item["state"] for item in qualification["runs"]} == {"queued"}


def test_admin_query_and_real_job_http_vertical_slice(kernel, attribution_release) -> None:
    token = login(kernel)
    auth = {"Authorization": f"Bearer {token}"}

    status, _, created = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/admin/users",
        headers=auth,
        payload={
            "username": "operator",
            "password": "Operator-password-123",
            "role": "operator",
        },
    )
    assert status == 201
    assert set(created) == {"user_id", "username", "role", "active", "mfa_enabled", "created_at"}

    operator_token = login(
        kernel,
        username="operator",
        password="Operator-password-123",
    )
    operator_auth = {"Authorization": f"Bearer {operator_token}"}
    status, _, operator_me = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/auth/me",
        headers=operator_auth,
    )
    assert status == 200
    assert operator_me == {
        "user_id": created["user_id"],
        "username": "operator",
        "role": "operator",
        "mfa_enabled": False,
    }
    status, _, gate_facts = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/quanthus/gates",
        headers=operator_auth,
    )
    assert status == 200
    assert gate_facts["port_version"] == "quanthus.gate-query.v1"
    status, _, catalog = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/product/catalog",
        headers=operator_auth,
    )
    assert status == 200
    assert catalog["schema_version"] == "quanthus.product-research-catalog.v1"
    assert catalog["research_only"] is True
    assert catalog["execution_authorized"] is False
    assert catalog["data_scope"]["dwd_base"]["through_date"] == "2026-07-31"
    assert catalog["data_scope"]["replay_partitions"]["recent_research"] == {
        "start": "2026-01-01",
        "end": "2026-07-30",
        "status": "accepted-available",
    }
    assert all(item["kind"] == "candidate-version" for item in catalog["strategies"])
    assert all(
        item["approval"]["replay"]["state"] == "replay-succeeded"
        and item["approval"]["review"]["state"] == "pending-review"
        and item["approval"]["governance"]["state"] == "governance-not-submitted"
        and item["approval"]["execution"] == {
            "state": "not-authorized",
            "authorized": False,
        }
        for item in catalog["strategies"]
    )
    assert not any(item["execution_authorized"] for item in catalog["strategies"])
    assert all(
        item["evidence_scope"]["partition"] == "development"
        for item in catalog["strategies"]
    )
    assert [item["strategy_id"] for item in catalog["strategies"]] == [
        "candidate.market-breadth-trend-breakout",
        "baseline.ma-trend",
        "baseline.breakout20",
        "baseline.oversold-rebound",
            "quantdinger.strategy-v2-bullish-three-lines",
            "candidate.p4-trend-risk",
        ]

    job_headers = {
        **operator_auth,
        "Idempotency-Key": "http-verify-release",
    }
    status, _, job = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/jobs/attribution-verifications",
        headers=job_headers,
        payload={"release_id": attribution_release.name},
    )
    assert status == 201
    terminal = kernel.jobs.wait(job["job_id"], timeout=10)
    assert terminal.state == "succeeded"
    assert terminal.result_ref is not None
    assert terminal.result_ref["release_digest"].startswith("sha256:")

    duplicate_status, _, duplicate = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/jobs/attribution-verifications",
        headers=job_headers,
        payload={"release_id": attribution_release.name},
    )
    assert duplicate_status == 200
    assert duplicate["job_id"] == job["job_id"]


def test_candidate_replay_submission_list_and_cancel_http_slice(kernel) -> None:
    authorization = f"Bearer {login(kernel)}"
    status, _, submitted = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/product/candidate-replays",
        headers={
            "Authorization": authorization,
            "Idempotency-Key": "candidate-replay-http-slice",
        },
        payload={"reason": "验证持久化队列的提交、查询与取消纵向链路。"},
    )
    assert status == 201
    assert submitted["kind"] == "quanthus.candidate-replay.v1"
    assert submitted["queue"] == "replay"
    assert submitted["state"] == "queued"
    assert submitted["input_ref"]["provider_config"] == (
        "config/replay-data-provider.development.v2.json"
    )
    assert set(submitted["input_ref"]["expected_active_runs"].values()) == {None}
    assert len(submitted["input_ref"]["expected_active_runs"]) == 5

    status, _, listed = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/product/candidate-replays",
        headers={"Authorization": authorization},
    )
    assert status == 200
    assert [job["job_id"] for job in listed["jobs"]] == [submitted["job_id"]]

    status, _, canceled = wsgi_request(
        kernel,
        "POST",
        f"/api/platform/v1/jobs/{submitted['job_id']}/cancel",
        headers={"Authorization": authorization},
    )
    assert status == 200
    assert canceled["state"] == "canceled"

    status, _, listed = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/product/candidate-replays",
        headers={"Authorization": authorization},
    )
    assert status == 200
    assert listed["jobs"][0]["state"] == "canceled"


def test_viewer_is_denied_job_submission_and_mfa_is_enforced(kernel, monkeypatch) -> None:
    admin_token = login(kernel)
    admin_auth = {"Authorization": f"Bearer {admin_token}"}
    wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/admin/users",
        headers=admin_auth,
        payload={
            "username": "viewer",
            "password": "Viewer-password-123",
            "role": "viewer",
        },
    )
    viewer_token = login(kernel, username="viewer", password="Viewer-password-123")
    assert (
        wsgi_request(
            kernel,
            "POST",
            "/api/platform/v1/jobs/attribution-verifications",
            headers={"Authorization": f"Bearer {viewer_token}", "Idempotency-Key": "denied"},
            payload={"release_id": "anything"},
        )[0]
        == 403
    )

    observed_time = 1_800_000_000
    monkeypatch.setattr(kernel.identity, "_wall_clock", lambda: observed_time)
    status, _, enrollment = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/auth/mfa/enroll",
        headers=admin_auth,
        payload={},
    )
    assert status == 200
    code = totp_code(enrollment["secret"], at=observed_time)
    assert (
        wsgi_request(
            kernel,
            "POST",
            "/api/platform/v1/auth/mfa/confirm",
            headers=admin_auth,
            payload={"code": code},
        )[0]
        == 200
    )
    status, _, challenge = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/auth/login",
        payload={"username": "admin", "password": "Admin-password-123"},
    )
    assert status == 428
    assert challenge["error"]["reason"] == "mfa_required"
    assert (
        wsgi_request(
            kernel,
            "POST",
            "/api/platform/v1/auth/login",
            payload={
                "username": "admin",
                "password": "Admin-password-123",
                "mfa_code": code,
            },
        )[0]
        == 200
    )


def test_metrics_expose_only_platform_runtime(kernel) -> None:
    wsgi_request(kernel, "GET", "/health")
    status, _, body = wsgi_request(kernel, "GET", "/metrics")
    rendered = body.decode("utf-8")
    assert status == 200
    assert "quanthus_platform_http_requests_total" in rendered
    assert "worker" not in rendered
    assert "celery" not in rendered


def test_candidate_detail_and_complete_approval_workflow(kernel) -> None:
    token = login(kernel)
    auth = {"Authorization": f"Bearer {token}"}
    candidate_path = "/api/platform/v1/product/candidates/baseline.ma-trend"

    status, _, detail = wsgi_request(kernel, "GET", candidate_path, headers=auth)
    assert status == 200
    assert detail["candidate"]["strategy_id"] == "baseline.ma-trend"
    assert len(detail["evidence"]["equity_curve"]) == 121
    assert len(detail["evidence"]["trades"]) == 240
    assert detail["run_history"] == []
    assert detail["approval"]["review"] == {
        "state": "pending-review",
        "available_actions": ["claim-review"],
    }
    assert detail["approval"]["governance"] == {
        "state": "governance-not-submitted",
        "package_id": None,
        "admitted": False,
        "terminal": False,
        "available_actions": [],
    }
    assert detail["approval"]["execution"] == {
        "state": "not-authorized",
        "authorized": False,
    }

    status, _, started = wsgi_request(
        kernel,
        "POST",
        candidate_path + "/reviews",
        headers=auth,
        payload={"action": "claim-review", "note": ""},
    )
    assert status == 201
    assert started["approval"]["review"]["state"] == "reviewing"
    assert started["approval"]["review"]["available_actions"] == [
        "request-changes",
        "approve-review",
        "reject-review",
    ]
    assert started["approval"]["execution"]["authorized"] is False

    assert (
        wsgi_request(
            kernel,
            "POST",
            candidate_path + "/reviews",
            headers=auth,
            payload={"action": "approve-review", "note": "短"},
        )[0]
        == 400
    )
    status, _, passed = wsgi_request(
        kernel,
        "POST",
        candidate_path + "/reviews",
        headers=auth,
        payload={
            "action": "approve-review",
            "note": "回放证据、成交明细和风险说明已完成复核。",
        },
    )
    assert status == 201
    assert passed["approval"]["review"]["state"] == "review-passed"
    assert passed["approval"]["governance"]["available_actions"] == [
        "submit-governance"
    ]

    status, _, submitted = wsgi_request(
        kernel,
        "POST",
        candidate_path + "/governance",
        headers=auth,
        payload={"action": "submit-governance", "note": "提交当前证据包进入治理审批。"},
    )
    assert status == 201
    first_package = submitted["approval"]["governance"]["package_id"]
    assert first_package.startswith("gpk_")
    assert submitted["approval"]["governance"]["state"] == "governance-under-review"
    assert submitted["approval"]["execution"]["authorized"] is False

    status, _, returned = wsgi_request(
        kernel,
        "POST",
        candidate_path + "/governance",
        headers=auth,
        payload={"action": "return-governance", "note": "风险说明需要重新复核并明确边界。"},
    )
    assert status == 201
    assert returned["approval"]["review"]["state"] == "pending-review"
    assert returned["approval"]["governance"]["state"] == "governance-returned"
    assert returned["approval"]["governance"]["available_actions"] == []

    for action, note in (
        ("claim-review", ""),
        ("approve-review", "风险边界已重新复核，证据说明满足要求。"),
    ):
        assert wsgi_request(
            kernel,
            "POST",
            candidate_path + "/reviews",
            headers=auth,
            payload={"action": action, "note": note},
        )[0] == 201
    status, _, resubmitted = wsgi_request(
        kernel,
        "POST",
        candidate_path + "/governance",
        headers=auth,
        payload={"action": "resubmit-governance", "note": "使用新的复核通过结论重新提交治理。"},
    )
    assert status == 201
    assert resubmitted["approval"]["governance"]["package_id"] != first_package

    status, _, rejected = wsgi_request(
        kernel,
        "POST",
        candidate_path + "/governance",
        headers=auth,
        payload={"action": "reject-governance", "note": "治理风险不可接受，拒绝当前候选版本。"},
    )
    assert status == 201
    assert rejected["approval"]["governance"]["state"] == "governance-rejected"
    assert rejected["approval"]["governance"]["terminal"] is True
    assert rejected["approval"]["review"]["available_actions"] == []
    assert rejected["approval"]["execution"]["authorized"] is False
    assert wsgi_request(
        kernel,
        "POST",
        candidate_path + "/governance",
        headers=auth,
        payload={"action": "admit-governance", "note": "不得覆盖已经作出的治理终态决定。"},
    )[0] == 400

    status, _, catalog = wsgi_request(
        kernel,
        "GET",
        "/api/platform/v1/product/catalog",
        headers=auth,
    )
    assert status == 200
    reviewed_row = next(
        item for item in catalog["strategies"] if item["strategy_id"] == "baseline.ma-trend"
    )
    assert reviewed_row["approval"]["review"]["state"] == "review-passed"
    assert reviewed_row["approval"]["governance"]["state"] == "governance-rejected"
    assert reviewed_row["approval"]["execution"]["authorized"] is False

    status, _, detail = wsgi_request(kernel, "GET", candidate_path, headers=auth)
    assert status == 200
    assert detail["approval"]["governance"]["state"] == "governance-rejected"
    assert len(detail["approval"]["history"]) == 8

    status, _, _ = wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/admin/users",
        headers=auth,
        payload={
            "username": "review-viewer",
            "password": "Viewer-password-123",
            "role": "viewer",
        },
    )
    assert status == 201
    viewer_token = login(
        kernel, username="review-viewer", password="Viewer-password-123"
    )
    viewer_auth = {"Authorization": f"Bearer {viewer_token}"}
    assert wsgi_request(kernel, "GET", candidate_path, headers=viewer_auth)[0] == 200
    assert (
        wsgi_request(
            kernel,
            "POST",
            candidate_path + "/reviews",
            headers=viewer_auth,
            payload={"action": "claim-review", "note": ""},
        )[0]
        == 403
    )
    assert (
        wsgi_request(
            kernel,
            "POST",
            candidate_path + "/governance",
            headers=viewer_auth,
            payload={"action": "submit-governance", "note": "只读角色不得提交治理请求。"},
        )[0]
        == 403
    )


def test_operator_can_review_but_cannot_govern(kernel) -> None:
    admin_auth = {"Authorization": f"Bearer {login(kernel)}"}
    assert wsgi_request(
        kernel,
        "POST",
        "/api/platform/v1/admin/users",
        headers=admin_auth,
        payload={
            "username": "approval-operator",
            "password": "Operator-password-123",
            "role": "operator",
        },
    )[0] == 201
    operator_token = login(
        kernel,
        username="approval-operator",
        password="Operator-password-123",
    )
    operator_auth = {"Authorization": f"Bearer {operator_token}"}
    candidate_path = "/api/platform/v1/product/candidates/baseline.breakout20"
    assert wsgi_request(
        kernel,
        "POST",
        candidate_path + "/reviews",
        headers=operator_auth,
        payload={"action": "claim-review", "note": ""},
    )[0] == 201
    assert wsgi_request(
        kernel,
        "POST",
        candidate_path + "/governance",
        headers=operator_auth,
        payload={"action": "submit-governance", "note": "操作员不得提交治理请求。"},
    )[0] == 403
