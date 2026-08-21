from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.platform.api import create_http_app
from quanthus.platform.manifest import (
    Feature,
    load_feature_manifest,
    validate_manifest_against_capability_gate,
)
from quanthus.platform.migrations import expected_tables
from quanthus.platform.models import UserRecord
from quanthus.policy import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_manifest_admits_only_minimal_runtime_and_operator_web_surfaces() -> None:
    manifest = load_feature_manifest()

    assert manifest.process_roles == (
        "api",
        "migration",
        "web",
        "replay-worker",
        "event-worker",
    )
    assert manifest.dependency_groups == ("platform",)
    assert manifest.migrations == (
        "identity",
        "candidate-review",
        "candidate-approval",
        "jobs",
    )
    assert manifest.web_routes == (
        "/",
        "/research",
        "/candidates",
        "/candidates/:strategy_id",
        "/attribution",
        "/operator",
    )
    validate_manifest_against_capability_gate(manifest)
    assert len(manifest.admitted_capabilities) == 11
    assert len(manifest.excluded_capabilities) == 18
    assert "desktop-operator-web" in manifest.admitted_capabilities
    assert "mcp-and-legacy-agent-tools" in manifest.excluded_capabilities
    assert "celery-redis-job-transport" in manifest.excluded_capabilities
    assert "ai-copilot-chat-tools" in manifest.excluded_capabilities
    assert "strategy-indicator-authoring" in manifest.excluded_capabilities
    assert all(
        "mcp" not in route and "ai" not in route and "broker" not in route
        for route in manifest.routes
    )
    assert all(
        fragment not in " ".join(manifest.web_routes).casefold()
        for fragment in ("mcp", "billing", "broker", "copilot", "fast-analysis")
    )


def test_platform_schema_contains_only_approved_control_state() -> None:
    manifest = load_feature_manifest()

    assert expected_tables(manifest.migrations) == (
        "platform_schema_migrations",
        "platform_users",
        "platform_sessions",
        "platform_security_audit",
        "platform_jobs",
        "platform_candidate_runs",
        "platform_candidate_active_runs",
        "platform_expert_evaluation_runs",
        "platform_candidate_reviews",
        "platform_candidate_approval_events",
    )
    fields = set(UserRecord.__dataclass_fields__)
    assert not fields.intersection(
        {"credits", "vip", "membership", "referral", "payment", "portfolio", "strategy"}
    )


def test_minimal_dependency_group_excludes_upstream_product_packages() -> None:
    raw = PROJECT_ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8")
    lowered = raw.casefold()
    platform_section = lowered.split("platform = [", 1)[1].split("]", 1)[0]

    assert all(
        package not in platform_section
        for package in (
            "celery",
            "redis",
            "litellm",
            "akshare",
            "yfinance",
            "finnhub",
            "ccxt",
            "talib",
            "alpaca",
            "ib_insync",
            "reportlab",
        )
    )


def test_docker_acceptance_runtime_is_disposable_and_not_host_exposed() -> None:
    compose = PROJECT_ROOT.joinpath("ops/platform/acceptance.compose.yaml").read_text(
        encoding="utf-8"
    )
    runner = PROJECT_ROOT.joinpath("ops/bin/run-platform-docker-acceptance").read_text(
        encoding="utf-8"
    )

    assert "image: postgres:16-alpine" in compose
    assert "internal: true" in compose
    assert "ports:" not in compose
    assert "/var/lib/postgresql/data" in compose
    assert "../..:/workspace:ro" in compose
    assert compose.count("read_only: true") == 1
    assert "down --volumes --remove-orphans" in runner
    assert "platform-kernel-postgres-runtime.json" in runner
    assert "platform-kernel-postgres-junit.xml" in runner
    assert "rm -f" in runner
    assert 'PROJECT_NAME="quanthus-pk-acceptance-20260812"' in runner


def test_production_docker_context_excludes_unneeded_or_restricted_surfaces() -> None:
    ignored = PROJECT_ROOT.joinpath(".dockerignore").read_text(encoding="utf-8").splitlines()
    dockerfile = PROJECT_ROOT.joinpath("ops/platform/Dockerfile").read_text(encoding="utf-8")
    builder = PROJECT_ROOT.joinpath("ops/bin/build-platform-image").read_text(encoding="utf-8")
    compose = PROJECT_ROOT.joinpath("ops/platform/compose.yaml").read_text(encoding="utf-8")

    assert {
        ".deps/",
        ".git/",
        ".tplan/",
        ".venv/",
        "artifacts/",
        "data/",
        "logs/",
        "tests/",
        "web/",
    }.issubset(ignored)
    assert "src/quanthus/event_library/" in ignored
    assert all(required not in ignored for required in ("config/", "src/"))
    assert "COPY config ./config" not in dockerfile
    assert "config/platform-features.v1.json config/platform-kernel-gates.v1.json" in dockerfile
    assert "config/product-research-catalog.v1.json" in dockerfile
    assert "config/product-candidate-evidence.v1.json" in dockerfile
    assert "chmod 0444 ./config/platform-features.v1.json" in dockerfile
    assert "COPY src ./src" not in dockerfile
    assert "COPY src/quanthus/platform ./src/quanthus/platform" in dockerfile
    assert all(
        forbidden not in dockerfile
        for forbidden in ("src/quanthus/providers", "src/quanthus/backtest", "event_library")
    )
    assert "mktemp -d /tmp/quanthus-platform-image.XXXXXX" in builder
    assert 'cp -a "$REPOSITORY_ROOT/src/quanthus/platform"' in builder
    assert '"$REPOSITORY_ROOT/config/product-research-catalog.v1.json"' in builder
    assert '"$REPOSITORY_ROOT/config/product-candidate-evidence.v1.json"' in builder
    assert all(
        forbidden not in builder
        for forbidden in ("src/quanthus/providers", "src/quanthus/backtest", "event_library")
    )
    assert "quanthus-platform-kernel:local" in compose
    assert "quanthus-platform-web:local" in compose
    assert "condition: service_healthy" in compose
    assert compose.count("healthcheck:") == 3
    assert "build:" not in compose


def test_event_worker_stays_a_control_plane_supervisor() -> None:
    worker = PROJECT_ROOT.joinpath("src/quanthus/platform/event_worker.py").read_text(
        encoding="utf-8"
    )
    builder = PROJECT_ROOT.joinpath("ops/bin/build-event-image").read_text(encoding="utf-8")
    dockerfile = PROJECT_ROOT.joinpath("ops/event/Dockerfile").read_text(encoding="utf-8")

    assert "quanthus.p4-event-scan.v1" not in worker
    assert "run-p4-reported-event-scan" in worker
    assert "verify-p4-event-scan" in worker
    assert "verify-p4-label-corpus" in worker
    assert "verify-p4-comparison-report" in worker
    assert all(
        forbidden not in worker
        for forbidden in (
            "from quanthus.event_library",
            "from quanthus.backtest",
            "from quanthus.pipelines",
        )
    )
    assert 'find "$REPOSITORY_ROOT/src/quanthus" -type f' in builder
    assert "event_library" not in builder.split("find", 1)[1].split("cp", 1)[0]
    assert 'RUN python -m pip install --no-cache-dir ".[p1,platform]"' in dockerfile


def test_operator_web_is_source_built_and_independently_removable() -> None:
    builder = PROJECT_ROOT.joinpath("ops/bin/build-platform-web-image").read_text(
        encoding="utf-8"
    )
    dockerfile = PROJECT_ROOT.joinpath("ops/platform/Web.Dockerfile").read_text(
        encoding="utf-8"
    )
    server = PROJECT_ROOT.joinpath("ops/platform/web-server.mjs").read_text(encoding="utf-8")

    assert 'cp -a "$REPOSITORY_ROOT/web/.openai"' in builder
    assert '"$REPOSITORY_ROOT/web/app"' in builder
    assert "COPY --from=builder" in dockerfile
    assert "npm run build" in dockerfile
    assert "COPY web/dist" not in dockerfile
    assert (
        'const proxiedPaths = ["/api/", "/health", "/ready", "/metrics", "/openapi.json"]'
        in server
    )
    assert 'const productRoot = resolve(serverRoot, "product-dist")' in server
    assert 'pathname.startsWith("/candidates/")' in server
    assert '"/strategies"' not in server
    assert '"/backtests"' not in server
    assert all(fragment not in server.casefold() for fragment in ("mcp", "broker", "billing"))


def test_platform_imports_do_not_bypass_quanthus_ports() -> None:
    platform_root = PROJECT_ROOT / "src" / "quanthus" / "platform"
    allowed_domain_imports = {
        "quanthus.policy",
        "quanthus.strategy_attribution_publication",
    }
    forbidden_fragments = ("providers", "backtest", "strategy_runtime", "broker", "execution")

    for path in sorted(platform_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("quanthus.")
            ):
                if (
                    node.module.startswith("quanthus.platform")
                    or node.module in allowed_domain_imports
                ):
                    continue
                assert not any(fragment in node.module for fragment in forbidden_fragments), (
                    path,
                    node.module,
                )


def test_feature_manifest_is_canonical_json() -> None:
    path = PROJECT_ROOT / "config" / "platform-features.v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == "quanthus.platform.features.v1"


def test_capability_omission_fails_against_frozen_gate() -> None:
    manifest = load_feature_manifest()
    identity = next(feature for feature in manifest.features if feature.id == "identity")
    truncated_identity = replace(
        identity,
        source_capabilities=identity.source_capabilities[1:],
    )
    truncated = replace(
        manifest,
        features=tuple(
            truncated_identity if feature.id == "identity" else feature
            for feature in manifest.features
        ),
    )

    with pytest.raises(ConfigurationError, match="capability coverage differs"):
        validate_manifest_against_capability_gate(truncated)


def test_unknown_manifest_route_fails_composition(kernel) -> None:
    manifest = kernel.manifest
    injected = Feature(
        id="invalid-route",
        enabled=True,
        source_capabilities=(),
        routes=("unknown.operation",),
        startup_hooks=(),
        migrations=(),
        web_routes=(),
    )
    invalid = replace(manifest, features=(*manifest.features, injected))

    with pytest.raises(RuntimeError, match="manifest and HTTP composition differ"):
        create_http_app(
            manifest=invalid,
            store=kernel.store,
            identity=kernel.identity,
                jobs=kernel.jobs,
                replay_jobs=kernel.replay_jobs,
                gate_query=kernel.gate_query,
                product_catalog=kernel.product_catalog,
                candidate_evidence=kernel.candidate_evidence,
                candidate_approvals=kernel.candidate_approvals,
                metrics=kernel.metrics,
        )


def test_deferred_or_removed_capability_cannot_be_admitted() -> None:
    manifest = load_feature_manifest()
    mcp_capability = "mcp-and-legacy-agent-tools"
    injected = Feature(
        id="invalid-mcp-admission",
        enabled=True,
        source_capabilities=(mcp_capability,),
        routes=(),
        startup_hooks=(),
        migrations=(),
        web_routes=(),
    )
    invalid = replace(
        manifest,
        features=(*manifest.features, injected),
        excluded_capabilities=tuple(
            capability
            for capability in manifest.excluded_capabilities
            if capability != mcp_capability
        ),
    )

    with pytest.raises(ConfigurationError, match="deferred or removed"):
        validate_manifest_against_capability_gate(invalid)
