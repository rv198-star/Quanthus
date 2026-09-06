from __future__ import annotations

import json

from quanthus.platform import api as api_module
from quanthus.platform.event_worker import (
    _seed_structure_facts_qualification_jobs,
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


def test_structure_mvp_review_sample_is_stratified_and_t_close_only(tmp_path) -> None:
    job_id = "job_" + "1" * 32
    run = tmp_path / "structure-runs" / ("run_" + "1" * 32)
    run.mkdir(parents=True)
    rows = [
        {
            "market_date": f"2020-01-0{index + 2}",
            "security_code": "600519",
            "state": state,
            "close": "10.10",
            "prior_high20": "10.00",
            "prior_low20": "9.00",
            "volume_ratio20": "1.30",
            "reason_code": reason,
            "knowledge_cutoff": f"2020-01-0{index + 2}T15:00:00+08:00",
            "future_return": "MUST_NOT_LEAK",
        }
        for index, (state, reason) in enumerate(
            (
                ("range_intact", "no_mvp_structure_trigger"),
                ("range_intact", "no_mvp_structure_trigger"),
                ("range_intact", "no_mvp_structure_trigger"),
                ("support_breach", "close_below_prior_low20"),
            )
        )
    ]
    (run / "structure-adm.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    sample = api_module._structure_mvp_review_sample(tmp_path, job_id)

    assert len(sample) == 3
    assert [row["state"] for row in sample].count("range_intact") == 2
    assert [row["state"] for row in sample].count("support_breach") == 1
    assert all("future_return" not in row for row in sample)


def test_structure_facts_review_sample_uses_preregistered_lifecycle_then_round_robin(
    tmp_path,
) -> None:
    job_id = "job_" + "2" * 32
    run = tmp_path / "structure-runs" / ("run_" + "2" * 32)
    run.mkdir(parents=True)
    securities = ("000001", "300750", "600519")

    def fact_row(code: str, day: int, status: str | None, age: int | None) -> dict[str, object]:
        has_episode = status is not None
        return {
            "market_date": f"2020-01-{day:02d}",
            "security_code": code,
            "dwd_security_id": f"dwd-{code}",
            "snapshot_id": f"snapshot-{code}-{day}",
            "knowledge_cutoff": f"2020-01-{day:02d}T15:00:00+08:00",
            "source_revision": "sha256:" + "1" * 64,
            "source_artifact_sha256": "sha256:" + "2" * 64,
            "source_close": "101",
            "available": True,
            "availability_reason": None,
            "history_reset": False,
            "source_window_start": "2019-12-01",
            "source_window_end": f"2020-01-{day - 1:02d}",
            "rolling_resistance20": "100",
            "rolling_support20": "90",
            "resistance_margin": "0.01",
            "support_margin": "0.1222222222222222222222222222",
            "volume_ratio20": "0.5",
            "episode_id": f"episode-{code}" if has_episode else None,
            "episode_status": status,
            "episode_termination_reason": (
                "close_below_breakout_level" if status == "ended" else None
            ),
            "attempt_at": "2020-01-02" if has_episode else None,
            "attempt_snapshot_id": f"attempt-{code}" if has_episode else None,
            "breakout_level": "100" if has_episode else None,
            "episode_support_level": "90" if has_episode else None,
            "attempt_volume_ratio20": "0.5" if has_episode else None,
            "episode_anchor_window_start": "2019-12-01" if has_episode else None,
            "episode_anchor_window_end": "2020-01-01" if has_episode else None,
            "breakout_margin": "0.01" if has_episode else None,
            "episode_support_margin": (
                "0.1222222222222222222222222222" if has_episode else None
            ),
            "post_attempt_hold_count": age if has_episode else None,
            "episode_age_sessions": age,
            "future_return": "MUST_NOT_LEAK",
            "coarse_state": "MUST_NOT_LEAK",
        }

    rows: list[dict[str, object]] = []
    for index, code in enumerate(securities):
        base = 2 + index * 4
        rows.extend(
            (
                fact_row(code, base, "active", 0),
                fact_row(code, base + 1, "active", 1),
                fact_row(code, base + 2, "ended", 2),
                fact_row(code, base + 3, None, None),
            )
        )
    (run / "structure-facts.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    sample = api_module._structure_facts_review_sample(tmp_path, job_id, securities)

    assert len(sample) == 12
    assert [(row["security_code"], row["episode_status"]) for row in sample[:9]] == [
        ("000001", "active"),
        ("000001", "active"),
        ("000001", "ended"),
        ("300750", "active"),
        ("300750", "active"),
        ("300750", "ended"),
        ("600519", "active"),
        ("600519", "active"),
        ("600519", "ended"),
    ]
    assert [row["security_code"] for row in sample[9:]] == list(securities)
    assert all("future_return" not in row and "coarse_state" not in row for row in sample)
    assert all(str(row["row_digest"]).startswith("sha256:") for row in sample)


def test_health_can_project_bounded_structure_facts_qualification(
    kernel, tmp_path, monkeypatch
) -> None:
    revision = "e" * 40
    job_ids = _seed_structure_facts_qualification_jobs(
        kernel.store,
        enabled_value="1",
        code_revision=revision,
    )
    config = tmp_path / "structure-facts-run.json"
    config.write_text(
        json.dumps({"security_codes": ["000001", "300750", "600519"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_module, "_STRUCTURE_FACTS_RUN_CONFIG_PATH", config)
    monkeypatch.setenv("QUANTHUS_STRUCTURE_FACTS_HEALTH_PROBE", "1")

    status, _, payload = wsgi_request(kernel, "GET", "/health")

    assert status == 200
    qualification = payload["structure_facts_qualification"]
    assert qualification["status"] == "running"
    assert qualification["code_revision"] == revision
    assert [item["job_id"] for item in qualification["runs"]] == list(job_ids)
    assert {item["state"] for item in qualification["runs"]} == {"queued"}


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
