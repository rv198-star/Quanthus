import json
import re
from collections import Counter
from pathlib import Path

from scripts.sync_github_planning import (
    CHAPTERS,
    PLATFORM_KERNEL_MILESTONE,
    WORKER_RUNTIME_MILESTONE,
    GitHub,
    work_items,
)

ROOT = Path(__file__).resolve().parents[2]
PK_ID = re.compile(r"^\|\s*(PK-[A-Z0-9-]+-[0-9]{3})\s*\|")


def _table_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PK_ID.match(line)
        if match:
            ids.add(match.group(1))
    return ids


def test_platform_kernel_gate_accepts_pk6_after_cutover_and_recovery() -> None:
    gate = json.loads(
        (ROOT / "config" / "platform-kernel-gates.v1.json").read_text(encoding="utf-8")
    )

    assert gate["relationship_to_delivery_phases"] == "orthogonal-to-P1-P7"
    assert gate["current_gate"] == "PK6"
    assert gate["current_status"] == "cutover-and-recovery-accepted"
    assert [item["id"] for item in gate["gates"]] == [
        "PK0",
        "PK1",
        "PK2",
        "PK3",
        "PK4",
        "PK5",
        "PK6",
    ]
    assert gate["gates"][0]["status"] == "accepted"
    assert gate["gates"][0]["acceptance_evidence"] == (
        "docs/acceptance/platform-kernel-pk0-boundary-freeze-20260812.md"
    )
    assert (ROOT / gate["gates"][0]["acceptance_evidence"]).is_file()
    acceptance = "docs/acceptance/platform-kernel-pk1-pk3-acceptance-20260812.md"
    assert all(item["status"] == "accepted" for item in gate["gates"][1:4])
    assert all(item["acceptance_evidence"] == acceptance for item in gate["gates"][1:4])
    assert (ROOT / acceptance).is_file()
    web_acceptance = "docs/acceptance/quantdinger-candidate-pilot-20260812.md"
    assert gate["gates"][4]["status"] == "accepted"
    assert gate["gates"][4]["acceptance_evidence"] == web_acceptance
    assert (ROOT / web_acceptance).is_file()
    assert gate["gates"][5]["status"] == "not-applicable"
    assert gate["gates"][5]["decision_scope"] == "first-cutover-only"
    assert gate["gates"][6]["status"] == "accepted"
    assert gate["gates"][6]["accepted_at"] == "2026-08-13"
    assert gate["gates"][6]["evidence"] == (
        "docs/acceptance/platform-kernel-pk6-cutover-recovery-20260813.md"
    )
    assert len(gate["pending_decisions"]) == 6
    assert all(item["status"] == "accepted" for item in gate["pending_decisions"])
    assert all(item["accepted_at"] == "2026-08-12" for item in gate["pending_decisions"])
    web_decision = next(
        item for item in gate["pending_decisions"] if item["id"] == "PK0-WEB-SOURCE"
    )
    assert web_decision["accepted_value"] == "pinned-quantdinger-vue-selective-derivative"
    assert gate["acceptance_results"]["capability_boundary_complete"] == "passed"
    assert gate["acceptance_results"]["minimal_kernel_only_admits_approved_surfaces"] == "passed"
    assert gate["acceptance_results"]["quanthus_authority_contracts_pass"] == "passed"
    assert gate["acceptance_results"]["operator_web_workflows_complete"] == "passed"
    assert gate["acceptance_results"]["authoring_isolated_or_not_applicable"] == "passed"
    assert gate["acceptance_results"]["replay_worker_producer_loop_complete"] == "passed"
    assert gate["acceptance_results"]["cutover_rollback_and_restoration_rehearsed"] == "passed"
    assert gate["work_items"]["PK-RECOVERY-001"] == "accepted"
    assert gate["work_items"]["PK-CUTOVER-001"] == "accepted"
    assert (ROOT / gate["gates"][6]["evidence"]).is_file()


def test_platform_kernel_capabilities_and_work_items_are_complete_and_unique() -> None:
    gate = json.loads(
        (ROOT / "config" / "platform-kernel-gates.v1.json").read_text(encoding="utf-8")
    )
    capabilities = gate["capabilities"]
    capability_ids = [item["id"] for item in capabilities]
    configured_work = set(gate["work_items"])

    assert len(capability_ids) == len(set(capability_ids))
    assert len(capabilities) == 29
    assert Counter(item["disposition"] for item in capabilities) == {
        "keep": 8,
        "replace": 4,
        "defer": 6,
        "remove": 11,
    }
    assert {item["disposition"] for item in capabilities} == {
        "keep",
        "replace",
        "defer",
        "remove",
    }
    assert all(
        item["target_owner"] and item["source_scope"] and item["restore_condition"]
        for item in capabilities
    )
    assert all(item["target_owner"] != "none" for item in capabilities)
    web = next(item for item in capabilities if item["id"] == "desktop-operator-web")
    assert web["disposition"] == "replace"
    assert web["selected_implementation"] == "pinned-quantdinger-vue-selective-derivative"
    assert web["upstream_source_admitted"] is True
    assert web["upstream_commit"] == "f2b5968bb815ed7c0c2301d61523dca98dcdddde"
    assert configured_work == _table_ids(ROOT / "docs" / "design" / "lld" / "11-platform-kernel.md")
    assert configured_work.issubset(_table_ids(ROOT / "docs" / "work-items" / "index.md"))


def test_platform_kernel_planning_closes_accepted_cutover_and_recovery() -> None:
    pk_chapters = [chapter for chapter in CHAPTERS if chapter.phase == "PK"]
    pk_items = [item for item in work_items(ROOT) if item.phase == "PK"]

    assert len(pk_chapters) == 1
    assert pk_chapters[0].milestone == PLATFORM_KERNEL_MILESTONE
    assert {item.stable_id for item in pk_items if item.status == "ready"} == set()
    assert {item.stable_id for item in pk_items if item.status == "closed"} == {
        "PK-BOUNDARY-001",
        "PK-COMPOSE-001",
        "PK-BOOT-001",
        "PK-SCHEMA-001",
        "PK-DEPS-001",
        "PK-AUTH-001",
        "PK-OBS-001",
        "PK-JOBS-001",
        "PK-ADAPTER-001",
        "PK-AUTHORITY-001",
        "PK-WEB-001",
        "PK-WEB-002",
        "PK-WEB-003",
        "PK-WEB-004",
        "PK-CANDIDATE-001",
        "PK-WORKER-000",
        "PK-WORKER-001",
        "PK-WORKER-002",
        "PK-WORKER-003",
        "PK-WORKER-004",
        "PK-WORKER-005",
        "PK-WORKER-006",
        "PK-WORKER-007",
        "PK-WORKER-008",
        "PK-RECOVERY-001",
        "PK-CUTOVER-001",
    }
    assert all(
        item.status == "blocked" for item in pk_items if item.status not in {"ready", "closed"}
    )
    worker_items = [item for item in pk_items if item.stable_id.startswith("PK-WORKER-")]
    assert {item.milestone for item in worker_items} == {WORKER_RUNTIME_MILESTONE}
    assert next(item for item in worker_items if item.stable_id == "PK-WORKER-000").issue_type == (
        "epic"
    )


def test_planning_sync_preserves_newer_managed_issue_body() -> None:
    existing = {
        "title": "[PK-WORKER-001] Existing",
        "body": "<!-- quanthus-planning:v2 id=PK-WORKER-001 kind=task -->\nRich contract",
        "labels": [],
        "milestone": None,
        "number": 80,
    }
    captured: dict[str, object] = {}
    github = GitHub("rv198-star/Quanthus")

    def fake_api(method: str, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {**existing, **payload}

    github.api = fake_api  # type: ignore[method-assign]
    github.upsert_issue(
        existing,
        "[PK-WORKER-001] Updated",
        "<!-- quanthus-planning:v1 id=PK-WORKER-001 kind=task -->\nGeneric body",
        ["type:task"],
        8,
    )

    assert captured["body"] == existing["body"]


def test_platform_kernel_authority_and_authoring_boundaries_are_explicit() -> None:
    hld = (ROOT / "docs" / "design" / "hld.md").read_text(encoding="utf-8")
    lld = (ROOT / "docs" / "design" / "lld" / "11-platform-kernel.md").read_text(encoding="utf-8")
    normalized_hld = " ".join(hld.split())
    normalized_lld = " ".join(lld.split())

    assert "Quanthus remains the sole owner" in normalized_hld
    assert "Platform endpoints call versioned Quanthus application ports" in normalized_lld
    assert "no direct edge to strategy registration" in normalized_lld
    assert "PK0 accepted confirmation set" in lld
    assert "QuantDinger-Vue@f2b5968bb815ed7c0c2301d61523dca98dcdddde" in lld


def test_platform_kernel_public_edge_exposes_only_the_web_service() -> None:
    compose = (ROOT / "ops" / "platform" / "compose.yaml").read_text(encoding="utf-8")

    assert compose.startswith("name: quanthus-platform\n")
    assert '"127.0.0.1:8080:8080"' in compose
    assert '"127.0.0.1:3100:3100"' in compose
    assert (
        "traefik.http.routers.quanthus.rule=Host(`quanthus.oci.061718.xyz`) "
        "&& !Path(`/metrics`)" in compose
    )
    assert "traefik.http.routers.quanthus.entrypoints=websecure" in compose
    assert "traefik.http.routers.quanthus.tls.certresolver=letsencrypt" in compose
    assert "traefik.http.services.quanthus-web.loadbalancer.server.port=3100" in compose
    assert compose.count("traefik.enable=true") == 1
    assert compose.count("restart: unless-stopped") == 4

    postgres = compose.split("  postgres:", 1)[1].split("\n  api:", 1)[0]
    api = compose.split("  api:", 1)[1].split("\n  replay-worker:", 1)[0]
    replay_worker = compose.split("  replay-worker:", 1)[1].split("\n  web:", 1)[0]
    assert "ports:" not in postgres
    assert "ports:" not in replay_worker
    assert "traefik-net" not in postgres
    assert "traefik-net" not in api
    assert "traefik-net" not in replay_worker
