from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from quanthus.platform.composition import create_platform_kernel
from quanthus.platform.identity import totp_code
from quanthus.platform.manifest import load_feature_manifest
from quanthus.platform.migrations import expected_tables
from quanthus.platform.persistence import PostgresPlatformStore
from quanthus.strategy_attribution_demo import (
    SyntheticAttributionExecutor,
    synthetic_run_request,
)
from quanthus.strategy_attribution_pipeline import AttributionPipeline
from quanthus.strategy_attribution_publication import AttributionPublisher


def _acceptance_dsn() -> str:
    value = os.environ.get("QUANTHUS_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("QUANTHUS_TEST_POSTGRES_DSN is not configured")
    return value


def _http_request(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    body = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    request_headers = dict(headers or {})
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(
        base_url + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        response = urlopen(request, timeout=10)  # noqa: S310 - fixed loopback acceptance URL
    except HTTPError as exc:
        response = exc
    with response:
        response_body = response.read()
        response_headers = {key.casefold(): value for key, value in response.headers.items()}
        content_type = response_headers.get("content-type", "")
        decoded: Any = (
            json.loads(response_body)
            if content_type.startswith("application/json")
            else response_body
        )
        return response.status, response_headers, decoded


def _write_acceptance_evidence(payload: Mapping[str, Any]) -> None:
    configured = os.environ.get("QUANTHUS_PLATFORM_ACCEPTANCE_EVIDENCE", "").strip()
    if not configured:
        return
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_ready(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Gunicorn exited before readiness with code {process.returncode}")
        try:
            status, _, payload = _http_request(base_url, "GET", "/ready")
            if status == 200 and payload == {
                "checks": {"postgres": True},
                "status": "ready",
            }:
                return
        except OSError as exc:
            last_error = exc
        time.sleep(0.1)
    raise TimeoutError(f"Gunicorn readiness timed out: {last_error}")


def test_postgres_clean_upgrade_and_real_http_workflow(tmp_path: Path) -> None:
    dsn = _acceptance_dsn()
    try:
        import psycopg  # type: ignore[import-not-found]
        from cryptography.fernet import Fernet  # type: ignore[import-not-found]
        from psycopg import sql
        from psycopg.conninfo import (  # type: ignore[import-not-found]
            conninfo_to_dict,
            make_conninfo,
        )
        from psycopg.rows import dict_row  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.fail(f"platform dependencies are required for PostgreSQL acceptance: {exc}")

    schema = "quanthus_platform_acceptance_" + uuid.uuid4().hex
    assert re.fullmatch(r"[a-z0-9_]+", schema)

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect_scoped(observed_dsn: str):  # type: ignore[no-untyped-def]
        connection = psycopg.connect(observed_dsn, row_factory=dict_row)
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return connection

    store = PostgresPlatformStore(dsn, connect=connect_scoped)
    manifest = load_feature_manifest()
    server_process: subprocess.Popen[bytes] | None = None
    server_log_path = tmp_path / "gunicorn.log"
    evidence: Mapping[str, Any] | None = None
    try:
        assert store.migrate(manifest.migrations) == (
            "identity-0001",
            "jobs-0001",
            "jobs-0002-lean-worker",
            "jobs-0003-candidate-runs",
            "jobs-0004-expert-evaluation-runs",
            "candidate-review-0001",
            "candidate-approval-0001",
        )
        assert store.migrate(manifest.migrations) == ()
        assert store.ready() is True
        with connect_scoped(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s ORDER BY table_name
                """,
                (schema,),
            )
            observed_tables = tuple(row["table_name"] for row in cursor.fetchall())
            cursor.execute(
                """
                SELECT module_id, migration_id FROM platform_schema_migrations
                ORDER BY module_id, migration_id
                """
            )
            ledger = tuple((row["module_id"], row["migration_id"]) for row in cursor.fetchall())
            cursor.execute("SHOW server_version")
            server_version = str(cursor.fetchone()["server_version"])
        assert observed_tables == tuple(sorted(expected_tables(manifest.migrations)))
        assert ledger == (
            ("candidate-approval", "candidate-approval-0001"),
            ("candidate-review", "candidate-review-0001"),
            ("identity", "identity-0001"),
            ("jobs", "jobs-0001"),
            ("jobs", "jobs-0002-lean-worker"),
                ("jobs", "jobs-0003-candidate-runs"),
                ("jobs", "jobs-0004-expert-evaluation-runs"),
        )

        request = synthetic_run_request()
        result = AttributionPipeline(tmp_path / "runs").run(
            request,
            SyntheticAttributionExecutor(),
        )
        release = AttributionPublisher(tmp_path / "releases").publish(request, result)
        kernel = create_platform_kernel(
            store=store,
            cipher_key=Fernet.generate_key().decode("ascii"),
            attribution_root=release.root.parent,
        )
        assert kernel.migrate() == ()
        admin = kernel.identity.create_user(
            username="acceptance-admin",
            password="Acceptance-admin-password-123",
            role="admin",
        )
        connection_parameters = conninfo_to_dict(dsn)
        connection_parameters["options"] = f"-csearch_path={schema}"
        scoped_dsn = make_conninfo(**connection_parameters)
        port = _unused_loopback_port()
        base_url = f"http://127.0.0.1:{port}"
        server_environment = {
            **os.environ,
            "QUANTHUS_PLATFORM_DATABASE_URL": scoped_dsn,
            "QUANTHUS_PLATFORM_MFA_KEY": Fernet.generate_key().decode("ascii"),
            "QUANTHUS_ATTRIBUTION_PUBLICATION_ROOT": str(release.root.parent),
        }
        server_log = server_log_path.open("wb")
        server_process = subprocess.Popen(  # noqa: S603 - fixed acceptance command
            (
                sys.executable,
                "-m",
                "gunicorn",
                f"--bind=127.0.0.1:{port}",
                "--workers=1",
                "quanthus.platform.server:create_app()",
            ),
            cwd=Path(__file__).resolve().parents[2],
            env=server_environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        server_log.close()
        _wait_for_ready(base_url, server_process)

        status, response_headers, health = _http_request(
            base_url,
            "GET",
            "/health",
            headers={"X-Request-ID": "postgres-runtime-acceptance"},
        )
        assert status == 200
        assert response_headers["x-request-id"] == "postgres-runtime-acceptance"
        assert health["status"] == "healthy"
        assert _http_request(base_url, "GET", "/ready")[2] == {
            "checks": {"postgres": True},
            "status": "ready",
        }
        assert _http_request(base_url, "GET", "/metrics")[0] == 200
        status, _, openapi = _http_request(base_url, "GET", "/openapi.json")
        assert status == 200
        rendered_openapi = json.dumps(openapi, ensure_ascii=True, sort_keys=True).casefold()
        assert all(
            fragment not in rendered_openapi
            for fragment in ("/mcp", "billing", "broker", "fast-analysis", "ai-chat")
        )
        assert _http_request(base_url, "GET", "/api/mcp/tools")[0] == 404

        status, _, login = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/auth/login",
            payload={
                "username": "acceptance-admin",
                "password": "Acceptance-admin-password-123",
            },
        )
        assert status == 200
        admin_token = str(login["token"])
        admin_auth = {"Authorization": f"Bearer {admin_token}"}
        status, _, created = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/admin/users",
            headers=admin_auth,
            payload={
                "username": "acceptance-operator",
                "password": "Acceptance-operator-password-123",
                "role": "operator",
            },
        )
        assert status == 201
        assert created["role"] == "operator"

        status, _, operator_login = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/auth/login",
            payload={
                "username": "acceptance-operator",
                "password": "Acceptance-operator-password-123",
            },
        )
        assert status == 200
        operator_token = str(operator_login["token"])
        operator_auth = {"Authorization": f"Bearer {operator_token}"}
        status, _, gate_facts = _http_request(
            base_url,
            "GET",
            "/api/platform/v1/quanthus/gates",
            headers=operator_auth,
        )
        assert status == 200
        assert gate_facts["port_version"] == "quanthus.gate-query.v1"

        candidate_id = "quantdinger.strategy-v2-bullish-three-lines"
        candidate_path = "/api/platform/v1/product/candidates/" + quote(
            candidate_id,
            safe="",
        )
        status, _, candidate_detail = _http_request(
            base_url,
            "GET",
            candidate_path,
            headers=operator_auth,
        )
        assert status == 200
        assert candidate_detail["candidate"]["strategy_id"] == candidate_id
        assert candidate_detail["approval"]["review"] == {
            "state": "pending-review",
            "available_actions": ["claim-review"],
        }
        assert candidate_detail["approval"]["governance"]["state"] == (
            "governance-not-submitted"
        )
        assert candidate_detail["approval"]["execution"]["authorized"] is False
        assert len(candidate_detail["evidence"]["equity_curve"]) == 121
        assert len(candidate_detail["evidence"]["trades"]) == 18

        status, _, review_started = _http_request(
            base_url,
            "POST",
            candidate_path + "/reviews",
            headers={**operator_auth, "X-Request-ID": "postgres-candidate-review"},
            payload={"action": "claim-review", "note": ""},
        )
        assert status == 201
        assert review_started["event"]["request_id"] == "postgres-candidate-review"
        assert review_started["approval"]["review"]["state"] == "reviewing"
        assert review_started["approval"]["execution"]["authorized"] is False

        status, _, review_passed = _http_request(
            base_url,
            "POST",
            candidate_path + "/reviews",
            headers=operator_auth,
            payload={
                "action": "approve-review",
                "note": "真实 PostgreSQL 证据复核通过，允许提交治理。",
            },
        )
        assert status == 201
        assert review_passed["approval"]["review"]["state"] == "review-passed"

        status, _, governance_submitted = _http_request(
            base_url,
            "POST",
            candidate_path + "/governance",
            headers=admin_auth,
            payload={
                "action": "submit-governance",
                "note": "将当前证据包提交至真实 PostgreSQL 治理流程。",
            },
        )
        assert status == 201
        package_id = governance_submitted["approval"]["governance"]["package_id"]
        assert package_id.startswith("gpk_")
        status, _, governance_rejected = _http_request(
            base_url,
            "POST",
            candidate_path + "/governance",
            headers=admin_auth,
            payload={
                "action": "reject-governance",
                "note": "验收流程选择拒绝，用于证明治理终态持久化。",
            },
        )
        assert status == 201
        assert governance_rejected["approval"]["governance"]["state"] == (
            "governance-rejected"
        )
        assert governance_rejected["approval"]["execution"]["authorized"] is False

        status, _, reviewed_candidate = _http_request(
            base_url,
            "GET",
            candidate_path,
            headers=operator_auth,
        )
        assert status == 200
        assert reviewed_candidate["approval"]["review"]["state"] == "review-passed"
        assert reviewed_candidate["approval"]["governance"]["state"] == (
            "governance-rejected"
        )
        assert len(reviewed_candidate["approval"]["history"]) == 4
        assert reviewed_candidate["approval"]["history"][0]["actor_username"] == (
            "acceptance-operator"
        )
        assert reviewed_candidate["approval"]["execution"]["authorized"] is False
        status, _, reviewed_catalog = _http_request(
            base_url,
            "GET",
            "/api/platform/v1/product/catalog",
            headers=operator_auth,
        )
        assert status == 200
        reviewed_row = next(
            item
            for item in reviewed_catalog["strategies"]
            if item["strategy_id"] == candidate_id
        )
        assert reviewed_row["approval"]["review"]["state"] == "review-passed"
        assert reviewed_row["approval"]["governance"]["state"] == (
            "governance-rejected"
        )
        assert reviewed_row["approval"]["execution"]["authorized"] is False

        job_headers = {
            **operator_auth,
            "Idempotency-Key": "postgres-runtime-attribution-verification",
        }
        status, _, submitted = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/jobs/attribution-verifications",
            headers=job_headers,
            payload={"release_id": release.release_id},
        )
        assert status == 201
        job_id = str(submitted["job_id"])
        deadline = time.monotonic() + 15
        terminal: Mapping[str, Any] = submitted
        while terminal["state"] not in {"succeeded", "failed", "canceled"}:
            assert time.monotonic() < deadline
            time.sleep(0.05)
            status, _, terminal = _http_request(
                base_url,
                "GET",
                f"/api/platform/v1/jobs/{job_id}",
                headers=operator_auth,
            )
            assert status == 200
        assert terminal["state"] == "succeeded"
        assert terminal["result_ref"]["port_version"] == ("quanthus.attribution-verification.v1")
        assert terminal["result_ref"]["release_digest"].startswith("sha256:")

        duplicate_status, _, duplicate = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/jobs/attribution-verifications",
            headers=job_headers,
            payload={"release_id": release.release_id},
        )
        assert duplicate_status == 200
        assert duplicate["job_id"] == job_id

        status, _, enrollment = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/auth/mfa/enroll",
            headers=admin_auth,
            payload={},
        )
        assert status == 200
        secret = str(enrollment["secret"])
        status, _, confirmation = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/auth/mfa/confirm",
            headers=admin_auth,
            payload={"code": totp_code(secret)},
        )
        assert status == 200
        assert confirmation == {"mfa_enabled": True}
        assert (
            _http_request(
                base_url,
                "POST",
                "/api/platform/v1/auth/logout",
                headers=admin_auth,
                payload={},
            )[0]
            == 200
        )

        status, _, mfa_challenge = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/auth/login",
            payload={
                "username": "acceptance-admin",
                "password": "Acceptance-admin-password-123",
            },
        )
        assert status == 428
        assert mfa_challenge["error"]["reason"] == "mfa_required"
        status, _, mfa_login = _http_request(
            base_url,
            "POST",
            "/api/platform/v1/auth/login",
            payload={
                "username": "acceptance-admin",
                "password": "Acceptance-admin-password-123",
                "mfa_code": totp_code(secret),
            },
        )
        assert status == 200
        assert (
            _http_request(
                base_url,
                "POST",
                "/api/platform/v1/auth/logout",
                headers={"Authorization": f"Bearer {mfa_login['token']}"},
                payload={},
            )[0]
            == 200
        )
        assert (
            _http_request(
                base_url,
                "POST",
                "/api/platform/v1/auth/logout",
                headers=operator_auth,
                payload={},
            )[0]
            == 200
        )
        assert (
            _http_request(
                base_url,
                "GET",
                "/api/platform/v1/auth/me",
                headers=operator_auth,
            )[0]
            == 401
        )

        with connect_scoped(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT mfa_secret_ciphertext FROM platform_users WHERE user_id = %s",
                (admin.user_id,),
            )
            ciphertext = str(cursor.fetchone()["mfa_secret_ciphertext"])
            cursor.execute("SELECT COUNT(*) AS count FROM platform_jobs")
            job_count = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM platform_candidate_reviews")
            candidate_review_count = int(cursor.fetchone()["count"])
            cursor.execute(
                "SELECT COUNT(*) AS count FROM platform_candidate_approval_events"
            )
            candidate_approval_count = int(cursor.fetchone()["count"])
            cursor.execute(
                """
                SELECT package_id FROM platform_candidate_approval_events
                WHERE axis = 'governance' ORDER BY created_at, event_id
                """
            )
            governance_packages = tuple(row["package_id"] for row in cursor.fetchall())
            cursor.execute(
                "SELECT DISTINCT event_type FROM platform_security_audit ORDER BY event_type"
            )
            audit_events = tuple(row["event_type"] for row in cursor.fetchall())
        assert secret not in ciphertext
        assert job_count == 1
        assert candidate_review_count == 0
        assert candidate_approval_count == 4
        assert governance_packages == (package_id, package_id)
        assert {"login", "logout", "mfa.enabled", "user.created"}.issubset(audit_events)

        evidence = {
            "schema_version": "quanthus.platform.acceptance.v1",
            "status": "passed",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "manifest_version": manifest.version,
            "postgresql": {
                "server_version": server_version,
                "migrations": [list(item) for item in ledger],
                "second_migration_run": [],
                "tables": list(observed_tables),
                "schema_cleanup": "passed",
            },
            "http": {
                "transport": "gunicorn-wsgi",
                "workers": 1,
                "ready": True,
                "request_id": "postgres-runtime-acceptance",
                "openapi_paths": sorted(openapi["paths"]),
                "removed_route_status": 404,
            },
            "workflow": {
                "authentication": "passed",
                "mfa": "passed",
                "rbac": "passed",
                "gate_query_port": gate_facts["port_version"],
                "attribution_verification_port": terminal["result_ref"]["port_version"],
                "job_state": terminal["state"],
                "job_count_after_duplicate": job_count,
                "candidate_detail": "passed",
                "candidate_review_state": reviewed_candidate["approval"]["review"][
                    "state"
                ],
                "candidate_governance_state": reviewed_candidate["approval"][
                    "governance"
                ]["state"],
                "legacy_candidate_review_count": candidate_review_count,
                "candidate_approval_event_count": candidate_approval_count,
                "governance_admitted": False,
                "execution_authorized": False,
                "logout": "passed",
                "encrypted_mfa_secret": True,
            },
        }
    finally:
        if server_process is not None and server_process.poll() is None:
            server_process.terminate()
            try:
                server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait(timeout=5)
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    assert evidence is not None
    _write_acceptance_evidence(evidence)
