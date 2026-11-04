"""Prepare synthetic inputs for a real, pinned namespace/controller qualification.

Generation uses development test dependencies. The subsequent build must run in
the separate locked runtime without pytest, mock mounts or access to seed TMP.
This fixture is not the real Base 2.5 data or complete continuous-version A.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base_build_profile import delivery_contract
from tests.base_build_full_fixture import native_predecessor, ods_predecessor
from tests.test_base_build_ods import save
from tests.test_base_build_period import fixture
from tests.unit.test_base24_ods_image import source_closure
from tests.unit.test_native5m_daily_source import original


def seed_only_paths(root: Path, selected: list[Path], controls: Path) -> list[Path]:
    result = []

    def visit(path: Path) -> None:
        if path == controls or any(path == p or p in path.parents for p in selected):
            return
        if any(path in p.parents for p in selected):
            for child in sorted(path.iterdir()):
                visit(child)
        else:
            result.append(path)

    for child in sorted(root.iterdir()):
        visit(child)
    return result


def prepare(
    *,
    root: Path,
    job_root: Path,
    audit_parent: Path,
    revision: str,
    runtime_python: str,
    runtime_user: str,
    wheelhouse: str,
    protected: list[str],
) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=False)
    closure_area = root / "legacy-ods-generation"
    closure_area.mkdir()
    raw_area = root / "original-wire-generation"
    raw_area.mkdir()
    closure = source_closure.__wrapped__(closure_area)
    wire = original.__wrapped__(raw_area)
    # Mocking is confined to construction of the sealed synthetic predecessor.
    # Undo all hooks before returning any files to the locked-runtime process.
    with pytest.MonkeyPatch.context() as patch:
        args = fixture(
            root,
            wire,
            base_factory=lambda source, schemas: native_predecessor(source, schemas, patch),
            ods_factory=lambda: ods_predecessor(root, closure),
        )
    controls = root / "controls"
    controls.mkdir()
    frozen, roots = args["inputs"].frozen, args["inputs"].roots
    save(controls / "selection.json", frozen["content"]["selection"])
    save(controls / "frozen.json", frozen)
    save(controls / "roots.json", roots)
    job = json.loads(Path("config/pipelines/base-build-job.example.v1.json").read_bytes())
    job.update(
        builder_revision=revision,
        selection=str(controls / "selection.json"),
        frozen=str(controls / "frozen.json"),
        roots=str(controls / "roots.json"),
        work_root=str(job_root),
        environment_lock=str(Path("ops/data/base-build-environment.v1.json").resolve()),
        wheelhouse=wheelhouse,
        limits={
            "allocated_bytes": 512 * 1024**2,
            "temp_bytes": 128 * 1024**2,
            "free_floor_bytes": 1024**3,
            "rss_bytes": 4 * 1024**3,
            "wall_seconds": 900,
        },
        **{k: args[k] for k in ("source_evidence_id", "calendar_reference", "closed_days")},
    )
    contract = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.5",
        predecessor_version="2.4",
        through="2026-10-09",
    )
    execution = json.loads(
        Path("config/pipelines/base-build-execution.example.v1.json").read_bytes()
    )
    execution.update(
        runtime_python=runtime_python,
        runtime_user=runtime_user,
        audit_parent=str(audit_parent),
        protected_paths=sorted(
            set(
                [
                    *protected,
                    *(
                        str(p)
                        for p in seed_only_paths(root, [Path(p) for p in roots.values()], controls)
                    ),
                ]
            )
        ),
        audit_limits={
            "allocated_bytes": 512 * 1024**2,
            "temp_bytes": 128 * 1024**2,
            "free_floor_bytes": 1024**3,
            "rss_bytes": 4 * 1024**3,
            "wall_seconds": 900,
        },
        service_limits={
            "memory_max_bytes": 5 * 1024**3,
            "cpu_quota_percent": 200,
            "wall_seconds": 1800,
        },
    )
    for name, value in (("job", job), ("contract", contract), ("execution", execution)):
        save(controls / (name + ".json"), value)
    save(
        controls / "SYNTHETIC-QUALIFICATION.json",
        {
            "synthetic": True,
            "real_base25_data": False,
            "full_A_B_C": False,
            "generation_code_revision": revision,
            "mocking": "PREDECESSOR_GENERATION_ONLY_SUBSEQUENT_LOCKED_RUNTIME_UNMOCKED",
            "denied_seed_paths": [
                str(p) for p in seed_only_paths(root, [Path(p) for p in roots.values()], controls)
            ],
        },
    )
    return {name: str(controls / (name + ".json")) for name in ("job", "contract", "execution")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("root", "job-root", "audit-parent"):
        parser.add_argument("--" + option, type=Path, required=True)
    for option in ("revision", "runtime-python", "runtime-user", "wheelhouse"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--protected", action="append", required=True)
    parser.add_argument("--predecessor-controls", type=Path)
    args = vars(parser.parse_args())
    previous = args.pop("predecessor_controls")
    print(json.dumps(prepare_next(previous=previous, **args) if previous else prepare(**args)))


def prepare_next(*, previous: Path, **args) -> dict[str, str]:
    """Prepare the next Job using the first controller's actual sealed pair."""
    from quanthus.pipelines.securities_warehouse.base_build_freeze import freeze
    from tests.base_build_full_fixture import standard_next_inputs

    root, job_root, audit_parent = (args[k] for k in ("root", "job_root", "audit_parent"))
    revision = args["revision"]
    prior_job = json.loads((previous / "job.json").read_bytes())
    prior_execution = json.loads((previous / "execution.json").read_bytes())
    pair = Path(prior_job["work_root"]) / "published"
    assert (pair.parent / "PAIR-READY.json").is_file()
    root.mkdir(parents=True, exist_ok=False)
    selection, roots, configuration = standard_next_inputs(
        root, pair / "ODS", pair / "DWD", revision
    )
    controls = root / "controls"
    controls.mkdir()
    frozen = freeze(selection, roots)
    for name, value in (("selection", selection), ("roots", roots), ("frozen", frozen)):
        save(controls / (name + ".json"), value)
    job = dict(prior_job)
    job.update(
        builder_revision=revision,
        work_root=str(job_root),
        selection=str(controls / "selection.json"),
        roots=str(controls / "roots.json"),
        frozen=str(controls / "frozen.json"),
        **configuration,
    )
    execution = dict(prior_execution)
    execution.update(
        audit_parent=str(audit_parent),
        protected_paths=sorted(
            set(
                [
                    *args["protected"],
                    *(
                        str(p)
                        for p in seed_only_paths(root, [Path(p) for p in roots.values()], controls)
                    ),
                ]
            )
        ),
    )
    contract = delivery_contract(
        json.loads(Path("config/pipelines/base24-delivery.v1.json").read_bytes()),
        version="2.6",
        predecessor_version="2.5",
        through="2026-10-13",
    )
    for name, value in (("job", job), ("execution", execution), ("contract", contract)):
        save(controls / (name + ".json"), value)
    save(
        controls / "SYNTHETIC-QUALIFICATION.json",
        {
            "synthetic": True,
            "real_base25_data": False,
            "full_A_B_C": False,
            "generation_code_revision": revision,
            "predecessor": str(pair),
            "mocking": "NONE_SECOND_VERSION",
            "inputs": "SEALED_FIRST_PAIR_AND_SELF_CONTAINED_CALENDAR_DAY_DELIVERIES",
        },
    )
    return {name: str(controls / (name + ".json")) for name in ("job", "contract", "execution")}


if __name__ == "__main__":
    main()
