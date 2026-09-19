"""Independent first-official-cycle reproduction for P6 activation acceptance.

The reproduction re-executes the exact first official input in a separate artifact inventory.  It
does not count as prospective evidence and cannot advance the 66-session budget.  A mismatch fails
closed before any later official session may be admitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from quanthus.forward_validation.activation import P6ProspectiveRuntimeConfig
from quanthus.forward_validation.contracts import fail
from quanthus.forward_validation.official_cycle import OfficialCycleJobInput, run_official_cycle
from quanthus.forward_validation.runner import verify_forward_run
from quanthus.forward_validation.serialization import canonical_json_bytes
from quanthus.platform.jobs import input_digest

_INPUT_SCHEMA = "quanthus.p6-official-reproduction-input.v1"
_RUN_SCHEMA = "quanthus.p6-official-reproduction-run.v1"
_ARTIFACT_SCHEMA = "quanthus.p6-official-reproduction.v1"


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        fail("invalid_digest", field)
    return value


@dataclass(frozen=True)
class OfficialCycleReproductionJobInput:
    source_input: OfficialCycleJobInput
    primary_run_id: str
    primary_manifest_digest: str

    def __post_init__(self) -> None:
        if self.source_input.prior_run_id is not None:
            fail(
                "reproduction_scope_drift",
                "P6 activation acceptance reproduces only the first official cycle",
            )
        if not self.primary_run_id.startswith("p6_run_"):
            fail("field_mismatch", "primary_run_id is invalid")
        _digest(self.primary_manifest_digest, "primary_manifest_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _INPUT_SCHEMA,
            "mode": "official_reproduction",
            "source_input": self.source_input.to_dict(),
            "primary_run_id": self.primary_run_id,
            "primary_manifest_digest": self.primary_manifest_digest,
        }

    @property
    def input_digest(self) -> str:
        return input_digest(self.to_dict())

    @property
    def idempotency_key(self) -> str:
        return f"p6-official-reproduction:{self.input_digest[7:39]}"

    @property
    def concurrency_key(self) -> str:
        return "p6-forward-cycle:official-reproduction"

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> OfficialCycleReproductionJobInput:
        expected = {
            "schema_version",
            "mode",
            "source_input",
            "primary_run_id",
            "primary_manifest_digest",
        }
        if (
            set(value) != expected
            or value.get("schema_version") != _INPUT_SCHEMA
            or value.get("mode") != "official_reproduction"
        ):
            fail("field_mismatch", "official reproduction input fields differ")
        source = value.get("source_input")
        if not isinstance(source, Mapping):
            fail("field_mismatch", "source_input must be object")
        return cls(
            source_input=OfficialCycleJobInput.from_dict(source),
            primary_run_id=str(value.get("primary_run_id")),
            primary_manifest_digest=str(value.get("primary_manifest_digest")),
        )


@dataclass(frozen=True)
class OfficialCycleReproductionRun:
    root: Path
    run_id: str
    input_digest: str
    manifest_digest: str
    summary: Mapping[str, Any]


def _run_id(input_digest_value: str) -> str:
    return f"p6_repro_{input_digest_value[7:31]}"


def verify_official_reproduction(
    path: Path,
    *,
    expected_input_digest: str | None = None,
) -> OfficialCycleReproductionRun:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        artifact = json.loads((path / "reproduction.json").read_text(encoding="utf-8"))
        ready = (path / "READY").read_text(encoding="ascii").strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("p6_official_reproduction_unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != _RUN_SCHEMA
        or ready != _sha(path / "manifest.json")
        or manifest.get("reproduction_sha256") != _sha(path / "reproduction.json")
    ):
        raise RuntimeError("p6_official_reproduction_manifest_mismatch")
    if expected_input_digest is not None and manifest.get("input_digest") != expected_input_digest:
        raise RuntimeError("p6_official_reproduction_input_mismatch")
    if not isinstance(artifact, dict) or artifact.get("schema_version") != _ARTIFACT_SCHEMA:
        raise RuntimeError("p6_official_reproduction_artifact_mismatch")
    reproduced_root = path / "reproduced-run"
    reproduced = verify_forward_run(reproduced_root)
    if (
        artifact.get("semantic_match") is not True
        or artifact.get("reproduced_manifest_digest") != reproduced.manifest_digest
        or artifact.get("primary_manifest_digest") != reproduced.manifest_digest
        or artifact.get("source_input_digest") != reproduced.input_digest
    ):
        raise RuntimeError("p6_official_reproduction_semantic_mismatch")
    summary = manifest.get("summary")
    if (
        not isinstance(summary, dict)
        or summary.get("mode") != "official_reproduction"
        or summary.get("official_evidence") is not False
        or summary.get("semantic_match") is not True
        or summary.get("notification_count") != 0
        or summary.get("broker_action_count") != 0
        or summary.get("live_capital_action_count") != 0
    ):
        raise RuntimeError("p6_official_reproduction_authority_leak")
    return OfficialCycleReproductionRun(
        root=path.resolve(),
        run_id=str(manifest.get("run_id")),
        input_digest=str(manifest.get("input_digest")),
        manifest_digest=_sha(path / "manifest.json"),
        summary=MappingProxyType(dict(summary)),
    )


def find_official_reproduction_for_input(
    run_root: Path, input_digest_value: str
) -> OfficialCycleReproductionRun | None:
    if not run_root.exists():
        return None
    matches: list[OfficialCycleReproductionRun] = []
    for path in sorted(run_root.iterdir()):
        if not path.is_dir() or not path.name.startswith("p6_repro_"):
            raise RuntimeError("p6_official_reproduction_root_inventory_invalid")
        run = verify_official_reproduction(path)
        if run.input_digest == input_digest_value:
            matches.append(run)
    if len(matches) > 1:
        raise RuntimeError("non_deterministic_p6_official_reproduction")
    return matches[0] if matches else None


def run_official_reproduction(
    *,
    job_input: OfficialCycleReproductionJobInput,
    runtime: P6ProspectiveRuntimeConfig,
    staging: Path,
    primary_run_root: Path,
    reproduction_root: Path,
) -> OfficialCycleReproductionRun:
    input_digest_value = job_input.input_digest
    existing = find_official_reproduction_for_input(reproduction_root, input_digest_value)
    if existing is not None:
        return existing

    primary_path = (primary_run_root.resolve() / job_input.primary_run_id).resolve()
    if primary_path.parent != primary_run_root.resolve():
        fail("path_escape", "primary P6 run")
    primary = verify_forward_run(
        primary_path,
        expected_input_digest=job_input.source_input.input_digest,
    )
    if (
        primary.manifest_digest != job_input.primary_manifest_digest
        or primary.summary.get("mode") != "official_cycle"
    ):
        fail("binding_digest_drift", "primary official run differs")

    work = staging.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    build_root = work / "independent-build"
    reproduced = run_official_cycle(
        job_input=job_input.source_input,
        runtime=runtime,
        staging=work / "cycle-work",
        run_root=build_root,
    )

    primary_official_sha = _sha(primary.root / "official.json")
    reproduced_official_sha = _sha(reproduced.root / "official.json")
    primary_projection_sha = _sha(primary.root / "projection" / "manifest.json")
    reproduced_projection_sha = _sha(reproduced.root / "projection" / "manifest.json")
    semantic_match = (
        reproduced.manifest_digest == primary.manifest_digest
        and reproduced_official_sha == primary_official_sha
        and reproduced_projection_sha == primary_projection_sha
    )
    if not semantic_match:
        fail(
            "same_release_reproduction_semantic_mismatch",
            "independent first-official-cycle reproduction differs",
        )

    retained = work / "reproduced-run"
    os.replace(reproduced.root, retained)
    if build_root.exists():
        build_root.rmdir()

    artifact = {
        "schema_version": _ARTIFACT_SCHEMA,
        "job_kind": "quanthus.p6-forward-cycle.v1",
        "mode": "official_reproduction",
        "source_input_digest": job_input.source_input.input_digest,
        "market_date": job_input.source_input.market_date.isoformat(),
        "primary_run_id": primary.run_id,
        "primary_manifest_digest": primary.manifest_digest,
        "primary_official_sha256": primary_official_sha,
        "primary_projection_manifest_sha256": primary_projection_sha,
        "reproduced_manifest_digest": primary.manifest_digest,
        "reproduced_official_sha256": reproduced_official_sha,
        "reproduced_projection_manifest_sha256": reproduced_projection_sha,
        "semantic_match": True,
        "official_evidence": False,
    }
    (work / "reproduction.json").write_bytes(canonical_json_bytes(artifact) + b"\n")
    summary = {
        "mode": "official_reproduction",
        "official_evidence": False,
        "market_date": job_input.source_input.market_date.isoformat(),
        "primary_run_id": primary.run_id,
        "primary_manifest_digest": primary.manifest_digest,
        "reproduced_manifest_digest": primary.manifest_digest,
        "semantic_match": True,
        "dwd_read_count": 1,
        "notification_count": 0,
        "broker_action_count": 0,
        "live_capital_action_count": 0,
    }
    run_id = _run_id(input_digest_value)
    manifest = {
        "schema_version": _RUN_SCHEMA,
        "run_id": run_id,
        "input_digest": input_digest_value,
        "reproduction_sha256": _sha(work / "reproduction.json"),
        "summary": summary,
    }
    (work / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    (work / "READY").write_text(_sha(work / "manifest.json") + "\n", encoding="ascii")

    root = reproduction_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / run_id
    if final.exists():
        raise RuntimeError("immutable P6 official reproduction already exists")
    os.replace(work, final)
    return verify_official_reproduction(final, expected_input_digest=input_digest_value)
