from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from quanthus.platform.event_worker import (
    EventWorker,
    _bounded_child_failure,
    _canonical_digest,
    _seed_structure_economic_qualification_jobs,
    _seed_structure_facts_qualification_jobs,
    _seed_structure_mvp_qualification_jobs,
    _sha256,
    _structure_input,
    _structure_request_digest,
    _verify_structure_economic_artifact,
    _verify_structure_facts_artifact,
    _write_structure_qualification_seed_receipt,
)
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue, input_digest
from quanthus.platform.models import JobRecord, UserRecord
from quanthus.platform.persistence import InMemoryPlatformStore

STRUCTURE_INPUT = {
    "code_revision": "a" * 40,
    "run_config": "config/structure-mvp-run.development.v1.json",
}
ECONOMIC_INPUT = {
    "code_revision": "a" * 40,
    "run_config": "config/structure-economic-run.development.v1.json",
}
FACTS_INPUT = {
    "code_revision": "a" * 40,
    "run_config": "config/structure-facts-run.development.v1.json",
}


def _job(kind: str = STRUCTURE_MVP_JOB) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id="job_0123456789abcdef0123456789abcdef",
        kind=kind,
        actor_user_id="usr_structure_mvp",
        idempotency_key="structure-mvp-development-v1",
        input_ref=dict(STRUCTURE_INPUT),
        input_digest=input_digest(STRUCTURE_INPUT),
        queue="event",
        concurrency_key="structure-mvp:development-v1",
        state="running",
        stage="starting",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        claimed_by="event-test",
        claim_generation=2,
    )


class _Store:
    def __init__(self, job: JobRecord) -> None:
        self.job = job
        self.finished: list[dict[str, object]] = []

    def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
        self.claim_args = (queue, worker_id, lease_seconds)
        return self.job

    def finish_claimed_job(
        self,
        job_id: str,
        generation: int,
        *,
        state: str,
        result_ref: object,
        error_reason: object,
    ) -> None:
        self.finished.append(
            {
                "job_id": job_id,
                "generation": generation,
                "state": state,
                "result_ref": result_ref,
                "error_reason": error_reason,
            }
        )


def _qualification_user(
    *,
    user_id: str = "usr_structure_operator",
    username: str = "structure-operator",
    role: str = "operator",
    active: bool = True,
) -> UserRecord:
    return UserRecord(
        user_id=user_id,
        username=username,
        password_hash="not-used",
        role=role,
        active=active,
        mfa_secret_ciphertext=None,
        mfa_enabled=False,
        created_at=datetime.now(timezone.utc),
    )


class StructureMvpWorkerTests(unittest.TestCase):
    def test_bounded_child_failure_reports_only_the_last_bounded_stderr_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stderr.log"
            path.write_text(
                "trace line\nValueError: comparison population differs\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _bounded_child_failure(path, 1),
                "structure_mvp_child_exit_1:ValueError: comparison population differs",
            )
            path.write_text("x" * 10000 + "\nRuntimeError: final detail\n", encoding="utf-8")
            observed = _bounded_child_failure(path, 2)
            self.assertTrue(
                observed.startswith("structure_mvp_child_exit_2:RuntimeError: final detail")
            )
            self.assertLessEqual(len(observed), 500)

    def test_qualification_seed_is_disabled_by_default_and_rejects_invalid_values(self) -> None:
        store = InMemoryPlatformStore()
        self.assertEqual(
            _seed_structure_mvp_qualification_jobs(
                store,
                enabled_value="0",
                code_revision="a" * 40,
            ),
            (),
        )
        self.assertEqual(store.list_jobs(), ())
        with self.assertRaisesRegex(RuntimeError, "qualification_seed_invalid"):
            _seed_structure_mvp_qualification_jobs(
                store,
                enabled_value="yes",
                code_revision="a" * 40,
            )

    def test_economic_qualification_seed_is_fixed_and_default_off(self) -> None:
        store = InMemoryPlatformStore()
        store.create_user(_qualification_user())
        revision = "f" * 40
        self.assertEqual(
            _seed_structure_economic_qualification_jobs(
                store,
                enabled_value="0",
                code_revision=revision,
            ),
            (),
        )
        first = _seed_structure_economic_qualification_jobs(
            store,
            enabled_value="1",
            code_revision=revision,
        )
        second = _seed_structure_economic_qualification_jobs(
            store,
            enabled_value="1",
            code_revision=revision,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        jobs = store.list_jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(
            {item.input_ref["run_config"] for item in jobs},
            {"config/structure-economic-run.development.v1.json"},
        )
        self.assertEqual({item.kind for item in jobs}, {STRUCTURE_MVP_JOB})
        with self.assertRaisesRegex(RuntimeError, "economic_qualification_seed_invalid"):
            _seed_structure_economic_qualification_jobs(
                store,
                enabled_value="yes",
                code_revision=revision,
            )

    def test_facts_qualification_seed_is_fixed_and_default_off(self) -> None:
        store = InMemoryPlatformStore()
        store.create_user(_qualification_user())
        revision = "e" * 40
        self.assertEqual(
            _seed_structure_facts_qualification_jobs(
                store,
                enabled_value="0",
                code_revision=revision,
            ),
            (),
        )
        first = _seed_structure_facts_qualification_jobs(
            store,
            enabled_value="1",
            code_revision=revision,
        )
        second = _seed_structure_facts_qualification_jobs(
            store,
            enabled_value="1",
            code_revision=revision,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        jobs = store.list_jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(
            {item.input_ref["run_config"] for item in jobs},
            {"config/structure-facts-run.development.v1.json"},
        )
        self.assertEqual({item.kind for item in jobs}, {STRUCTURE_MVP_JOB})
        with self.assertRaisesRegex(RuntimeError, "facts_qualification_seed_invalid"):
            _seed_structure_facts_qualification_jobs(
                store,
                enabled_value="yes",
                code_revision=revision,
            )

    def test_qualification_seed_creates_exactly_two_idempotent_structure_jobs(self) -> None:
        store = InMemoryPlatformStore()
        store.create_user(_qualification_user())
        revision = "b" * 40
        first = _seed_structure_mvp_qualification_jobs(
            store,
            enabled_value="1",
            code_revision=revision,
        )
        second = _seed_structure_mvp_qualification_jobs(
            store,
            enabled_value="1",
            code_revision=revision,
        )
        self.assertEqual(first, second)
        expected_ids = tuple(
            "job_"
            + hashlib.sha256(
                f"{STRUCTURE_MVP_JOB}:{revision}:{suffix}".encode()
            ).hexdigest()[:32]
            for suffix in ("a", "b")
        )
        self.assertEqual(first, expected_ids)
        jobs = tuple(sorted(store.list_jobs(), key=lambda item: item.idempotency_key))
        self.assertEqual(len(jobs), 2)
        self.assertEqual({job.kind for job in jobs}, {STRUCTURE_MVP_JOB})
        self.assertEqual({job.queue for job in jobs}, {"event"})
        self.assertEqual(
            {job.input_ref["code_revision"] for job in jobs},
            {revision},
        )
        self.assertEqual(
            {job.input_ref["run_config"] for job in jobs},
            {"config/structure-mvp-run.development.v1.json"},
        )
        self.assertEqual(len({job.concurrency_key for job in jobs}), 2)
        self.assertEqual(
            {job.actor_user_id for job in jobs},
            {"usr_structure_operator"},
        )

    def test_qualification_seed_prefers_existing_mcp_service_principal(self) -> None:
        store = InMemoryPlatformStore()
        store.create_user(
            _qualification_user(
                user_id="usr_18c14af81a06cf878f177579efec3727",
                username="mcp-service",
                role="viewer",
                active=False,
            )
        )
        store.create_user(_qualification_user())
        _seed_structure_mvp_qualification_jobs(
            store,
            enabled_value="1",
            code_revision="e" * 40,
        )
        self.assertEqual(
            {item.actor_user_id for item in store.list_jobs()},
            {"usr_18c14af81a06cf878f177579efec3727"},
        )

    def test_qualification_seed_prefers_active_operator_and_fails_without_eligible_actor(
        self,
    ) -> None:
        store = InMemoryPlatformStore()
        store.create_user(
            _qualification_user(
                user_id="usr_admin",
                username="aaa-admin",
                role="admin",
            )
        )
        store.create_user(
            _qualification_user(
                user_id="usr_inactive_operator",
                username="aaa-operator",
                active=False,
            )
        )
        store.create_user(
            _qualification_user(
                user_id="usr_active_operator",
                username="zzz-operator",
            )
        )
        jobs = _seed_structure_mvp_qualification_jobs(
            store,
            enabled_value="1",
            code_revision="c" * 40,
        )
        self.assertEqual(len(jobs), 2)
        self.assertEqual(
            {item.actor_user_id for item in store.list_jobs()},
            {"usr_active_operator"},
        )

        empty = InMemoryPlatformStore()
        with self.assertRaisesRegex(RuntimeError, "qualification_actor_unavailable"):
            _seed_structure_mvp_qualification_jobs(
                empty,
                enabled_value="1",
                code_revision="d" * 40,
            )

    def test_durable_queue_accepts_only_well_formed_requested_job_id(self) -> None:
        store = InMemoryPlatformStore()
        store.create_user(_qualification_user())
        queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
        requested = "job_" + "1" * 32
        job, created = queue.submit(
            kind=STRUCTURE_MVP_JOB,
            actor_user_id="usr_structure_operator",
            idempotency_key="structure-requested-id",
            input_ref=STRUCTURE_INPUT,
            requested_job_id=requested,
        )
        self.assertTrue(created)
        self.assertEqual(job.job_id, requested)
        with self.assertRaisesRegex(ValueError, "requested_job_id"):
            queue.submit(
                kind=STRUCTURE_MVP_JOB,
                actor_user_id="usr_structure_operator",
                idempotency_key="structure-bad-requested-id",
                input_ref=STRUCTURE_INPUT,
                requested_job_id="job_NOT_HEX",
            )

    def test_qualification_seed_requires_exact_build_revision(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "qualification_revision_invalid"):
            _seed_structure_mvp_qualification_jobs(
                InMemoryPlatformStore(),
                enabled_value="1",
                code_revision="not-a-revision",
            )

    def test_seed_receipt_is_bounded_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs = ("job_" + "1" * 32, "job_" + "2" * 32)
            _write_structure_qualification_seed_receipt(
                root,
                code_revision="a" * 40,
                status="submitted",
                job_ids=jobs,
            )
            payload = json.loads(
                (root / "structure-qualification-seed.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["job_ids"], list(jobs))
            self.assertEqual(payload["status"], "submitted")
            self.assertIsNone(payload["reason"])

    def test_structure_input_is_exact_and_cannot_substitute_config(self) -> None:
        self.assertEqual(_structure_input(STRUCTURE_INPUT), STRUCTURE_INPUT)
        self.assertEqual(_structure_input(ECONOMIC_INPUT), ECONOMIC_INPUT)
        self.assertEqual(_structure_input(FACTS_INPUT), FACTS_INPUT)
        with self.assertRaisesRegex(RuntimeError, "run_config_not_governed"):
            _structure_input({**STRUCTURE_INPUT, "run_config": "/tmp/anything.json"})
        with self.assertRaisesRegex(RuntimeError, "input_contract_mismatch"):
            _structure_input({**STRUCTURE_INPUT, "command": "bash"})

    def test_structure_request_digest_accepts_all_governed_configs(self) -> None:
        self.assertTrue(
            _structure_request_digest(STRUCTURE_INPUT["run_config"]).startswith("sha256:")
        )
        self.assertEqual(
            _structure_request_digest(ECONOMIC_INPUT["run_config"]),
            "sha256:9a8a14a4855ddb189da2d8303c558a460ce781e63425942cda845e45e3ef66de",
        )
        self.assertTrue(
            _structure_request_digest(FACTS_INPUT["run_config"]).startswith("sha256:")
        )

    def test_economic_artifact_verifier_closes_semantic_and_file_digests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for variant in (
                "variant.native-baseline",
                "variant.structure-continuous",
                "variant.structure-coarse",
            ):
                path = root / "variants" / variant / "manifest.json"
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")
            code_revision = "a" * 40
            request_digest = "sha256:" + "b" * 64
            variants = {
                "variant.native-baseline": {
                    "metrics": {
                        "capital_constrained_net_total_return_delta": "0.06704618",
                        "maximum_drawdown_delta": "0.07102827",
                    }
                },
                "variant.structure-continuous": {"metrics": {}},
                "variant.structure-coarse": {"metrics": {}},
            }
            summary = {
                "schema_version": "quanthus.structure-economic-result.v1",
                "research_only": True,
                "execution_authorized": False,
                "shadow_authorized": False,
                "sealed_partition_access": False,
                "economic_claim_scope": "development_counterfactual_only",
                "code_revision": code_revision,
                "request_digest": request_digest,
                "structure_adm_manifest_digest": "sha256:" + "c" * 64,
                "variants": variants,
                "comparisons": {
                    "C1-C0": {},
                    "C2-C0": {},
                    "C2-C1": {},
                },
                "primary_comparison": "C2-C1",
                "lifecycle_decision": "no_material_structure_signal",
            }
            summary["result_digest"] = _canonical_digest(summary)
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
            files = {"summary.json": _sha256(summary_path)}
            for path in sorted((root / "variants").rglob("*")):
                if path.is_file():
                    files[path.relative_to(root).as_posix()] = _sha256(path)
            manifest = {
                "schema_version": "quanthus.structure-economic-artifact-manifest.v1",
                "research_only": True,
                "execution_authorized": False,
                "code_revision": code_revision,
                "request_digest": request_digest,
                "result_digest": summary["result_digest"],
                "structure_adm_manifest_digest": summary["structure_adm_manifest_digest"],
                "files": files,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            (root / "READY").write_text(
                json.dumps(
                    {
                        "schema_version": "quanthus.structure-economic-ready.v1",
                        "manifest_sha256": _sha256(manifest_path),
                        "result_digest": summary["result_digest"],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            result = _verify_structure_economic_artifact(
                root,
                code_revision=code_revision,
                request_digest=request_digest,
            )
            self.assertEqual(result["lifecycle_decision"], "no_material_structure_signal")
            tampered = root / "variants" / "variant.structure-coarse" / "manifest.json"
            tampered.write_text('{"tampered":true}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "artifact_digest_mismatch"):
                _verify_structure_economic_artifact(
                    root,
                    code_revision=code_revision,
                    request_digest=request_digest,
                )

    def test_facts_artifact_verifier_rejects_coarse_authority_in_fact_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code_revision = "a" * 40
            request_digest = "sha256:" + "b" * 64
            rows = [
                {
                    "market_date": "2020-01-02",
                    "security_code": "000001",
                    "dwd_security_id": "dwd-security-a",
                    "snapshot_id": "snapshot-a",
                    "knowledge_cutoff": "2020-01-02T15:00:00+08:00",
                    "source_revision": "sha256:" + "1" * 64,
                    "source_artifact_sha256": "sha256:" + "2" * 64,
                    "source_close": "10.1",
                    "available": True,
                    "availability_reason": None,
                    "history_reset": False,
                    "source_window_start": "2019-12-03",
                    "source_window_end": "2020-01-01",
                    "rolling_resistance20": "10",
                    "rolling_support20": "9",
                    "resistance_margin": "0.01",
                    "support_margin": "0.1222222222222222222222222222",
                    "volume_ratio20": "0.8",
                    "episode_id": "episode-a",
                    "episode_status": "active",
                    "episode_termination_reason": None,
                    "attempt_at": "2020-01-02",
                    "attempt_snapshot_id": "snapshot-a",
                    "breakout_level": "10",
                    "episode_support_level": "9",
                    "attempt_volume_ratio20": "0.8",
                    "episode_anchor_window_start": "2019-12-03",
                    "episode_anchor_window_end": "2020-01-01",
                    "breakout_margin": "0.01",
                    "episode_support_margin": "0.1222222222222222222222222222",
                    "post_attempt_hold_count": 0,
                    "episode_age_sessions": 0,
                }
            ]
            rows_path = root / "structure-facts.jsonl"
            rows_path.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows),
                encoding="utf-8",
            )
            adm_manifest = {
                "schema_version": "1.0.0",
                "adm_id": "structure-continuous-facts",
                "adm_version": "1.0.0",
                "research_only": True,
                "execution_authorized": False,
                "upstream_identity": "upstream-a",
                "policy_digest": "sha256:" + "c" * 64,
                "row_count": 1,
                "content_digest": _canonical_digest(rows),
                "allowed_use": "continuous_structure_fact_consumption",
                "forbidden_claims": [],
            }
            semantic_summary = {
                "schema_version": "quanthus.structure-facts-result.v1",
                "research_only": True,
                "execution_authorized": False,
                "economic_acceptance_claim": False,
                "coarse_renderer_authority": "explanation_only",
                "consumer_authority": "compatibility_proof_only",
                "request_digest": request_digest,
                "provider_identity": {},
                "adm_manifest": adm_manifest,
                "unavailable_counts": {},
                "fact_counts": {"rows": 1},
                "renderer_counts": {"breakout_attempt": 1},
                "consumer_counts": {"applicable": 1},
                "decisions": [],
                "p2_probe_semantics": "execution compatibility only; no portfolio/economic claim",
                "probes": [],
            }
            result_digest = _canonical_digest(semantic_summary)
            summary = {
                **semantic_summary,
                "result_digest": result_digest,
                "code_revision": code_revision,
            }
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
            manifest = {
                "schema_version": "quanthus.structure-facts-artifact-manifest.v1",
                "research_only": True,
                "execution_authorized": False,
                "code_revision": code_revision,
                "request_digest": request_digest,
                "files": {
                    "structure-facts.jsonl": _sha256(rows_path),
                    "summary.json": _sha256(summary_path),
                },
                "result_digest": result_digest,
                "adm_manifest_digest": _canonical_digest(adm_manifest),
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            (root / "READY").write_text(
                json.dumps(
                    {
                        "schema_version": "quanthus.structure-facts-ready.v1",
                        "manifest_sha256": _sha256(manifest_path),
                        "result_digest": result_digest,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            verified = _verify_structure_facts_artifact(
                root,
                code_revision=code_revision,
                request_digest=request_digest,
            )
            self.assertEqual(verified["row_count"], 1)

            rows[0]["state"] = "breakout_attempt"
            rows_path.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows),
                encoding="utf-8",
            )
            adm_manifest["content_digest"] = _canonical_digest(rows)
            semantic_summary["adm_manifest"] = adm_manifest
            tampered_result_digest = _canonical_digest(semantic_summary)
            tampered_summary = {
                **semantic_summary,
                "result_digest": tampered_result_digest,
                "code_revision": code_revision,
            }
            summary_path.write_text(
                json.dumps(tampered_summary, sort_keys=True),
                encoding="utf-8",
            )
            manifest["files"]["structure-facts.jsonl"] = _sha256(rows_path)
            manifest["files"]["summary.json"] = _sha256(summary_path)
            manifest["result_digest"] = tampered_result_digest
            manifest["adm_manifest_digest"] = _canonical_digest(adm_manifest)
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            (root / "READY").write_text(
                json.dumps(
                    {
                        "schema_version": "quanthus.structure-facts-ready.v1",
                        "manifest_sha256": _sha256(manifest_path),
                        "result_digest": tampered_result_digest,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "row_schema_invalid"):
                _verify_structure_facts_artifact(
                    root,
                    code_revision=code_revision,
                    request_digest=request_digest,
                )

    def test_event_worker_dispatches_structure_job_only_to_structure_executor(self) -> None:
        job = _job()
        store = _Store(job)
        with tempfile.TemporaryDirectory() as temporary:
            worker = EventWorker(
                store,  # type: ignore[arg-type]
                worker_id="event-test",
                run_root=Path(temporary),
                lease_seconds=300,
                renew_seconds=30.0,
                terminate_seconds=5.0,
            )
            with patch.object(worker, "_execute_structure") as execute_structure, patch.object(
                worker, "_execute"
            ) as execute_event:
                self.assertTrue(worker.run_once())
        execute_structure.assert_called_once_with(
            job.job_id,
            job.claim_generation,
            job.input_ref,
            job.input_digest,
        )
        execute_event.assert_not_called()
        self.assertEqual(store.claim_args, ("event", "event-test", 300))
        self.assertEqual(store.finished, [])

    def test_event_worker_still_rejects_unadmitted_event_queue_kind(self) -> None:
        job = _job("quanthus.unrelated.v1")
        store = _Store(job)
        with tempfile.TemporaryDirectory() as temporary:
            worker = EventWorker(
                store,  # type: ignore[arg-type]
                worker_id="event-test",
                run_root=Path(temporary),
                lease_seconds=300,
                renew_seconds=30.0,
                terminate_seconds=5.0,
            )
            self.assertTrue(worker.run_once())
        self.assertEqual(len(store.finished), 1)
        self.assertEqual(store.finished[0]["state"], "failed")
        self.assertEqual(store.finished[0]["error_reason"], "job_kind_not_admitted")


if __name__ == "__main__":
    unittest.main()
