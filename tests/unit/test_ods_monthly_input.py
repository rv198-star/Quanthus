"""Monthly ODS is a warehouse source, never implicit trade/research admission."""

import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines import ods_monthly as monthly
from quanthus.pipelines import ods_monthly_input as mod
from quanthus.pipelines.securities_warehouse.base22 import canonical, digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from quanthus.pipelines.securities_warehouse.planner import (
    WarehousePlanningError,
    build_input_plan,
)

MODEL = Path(__file__).resolve().parents[2] / "config/pipelines/dwd-domain-model.v1.json"


class Budget:
    def check(self):
        pass

    def begin_stage(self, name):
        pass


@pytest.fixture
def image(tmp_path, request):
    sources = []
    selection = [
        ("historical_seed", "daily_ohlcv_unadjusted"),
        ("core", "daily_ohlcv_none_delta"),
        ("events", "announcements_delta"),
    ]
    variant = getattr(request, "param", None)
    if variant == "unknown_nonempty":
        selection[-1] = ("events", "unknown_business_payload")
    elif variant == "two_revisions":
        selection.append(("events", "announcements_delta"))
    for number, (role, artifact) in enumerate(selection):
        root = tmp_path / (role + str(number))
        relative = (
            "payload/history.parquet" if role == "historical_seed" else "artifacts/raw.parquet"
        )
        path = root / relative
        path.parent.mkdir(parents=True)
        pq.write_table(
            pa.table(
                {
                    "id": ["SYNTHETIC"],
                    "precise": pa.array([Decimal("0.12345678901")], pa.decimal128(20, 11)),
                    "acquired_at": ["2026-10-08T21:39:35+08:00"],
                    "unknown": pa.array([None], pa.string()),
                    "zero": [0],
                }
            ),
            path,
        )
        entry = {
            "path": relative,
            "sha256": file_sha(path),
            "size_bytes": path.stat().st_size,
            "dataset_id": artifact,
            "role": "canonical_source",
            "format": "parquet",
        }
        if role == "historical_seed":
            content = {"files": [entry], "through_date": "2026-07-31"}
            manifest = {
                "schema_version": 1,
                "kind": "quanthus_ods_base_bundle",
                "content": content,
                "content_digest": digest(content),
            }
            name, identity = "manifest.json", "synthetic-seed"
        else:
            manifest = {
                "schema_version": 1,
                "release_id": "synthetic-" + role + str(number),
                "producer": "synthetic",
                "schedule": "daily",
                "status": "ready",
                "market_date": "2026-09-30",
                "created_at": "2026-10-09T01:00:00Z",
                "artifacts": [
                    {
                        "name": artifact,
                        "relative_path": relative,
                        "sha256": entry["sha256"],
                        "size_bytes": entry["size_bytes"],
                        "knowledge_cutoff": "2026-10-08T21:39:35+08:00",
                        "source_revision": "synthetic-v1",
                    }
                ],
            }
            name, identity = "release.json", manifest["release_id"]
        (root / name).write_bytes(canonical(manifest))
        sha = file_sha(root / name)
        (root / "READY").write_text(sha + "\n")
        sources.append(
            {
                "root": str(root),
                "role": role,
                "manifest_name": name,
                "identity": identity,
                "manifest_sha256": sha,
            }
        )
    raw_contract = {
        "contract_id": "synthetic-raw-monthly",
        "stage": "ACTIVE_VERSIONED_CONTRACT",
        "purpose": monthly.PURPOSE,
        "business_timezone": "Asia/Shanghai",
        "business_through": "2026-09-30",
        "calendar_authority": {"open_dates": ["2026-09-30"]},
        "historical_seed": {"manifest_sha256": sources[0]["manifest_sha256"]},
        "required_daily_roles": ["core", "events"],
        "required_artifacts": {
            "core": ["daily_ohlcv_none_delta"],
            "events": [
                "unknown_business_payload"
                if variant == "unknown_nonempty"
                else "announcements_delta"
            ],
        },
        "admitted_sources": {s["manifest_sha256"]: {"role": s["role"]} for s in sources},
        "intraday": {
            "availability_gaps": [
                {
                    "business_date": "2026-09-30",
                    "role": "intraday_1m",
                    "reason": "SYNTHETIC_NOT_AVAILABLE",
                }
            ]
        },
    }
    plan = monthly.freeze_monthly_plan({"selected": sources}, raw_contract)
    root = tmp_path / "image"
    scan = monthly.materialize_monthly(plan, root, Budget())
    code_revision = "a" * 40
    receipts = [
        {
            "pid": pid,
            "identity": scan["identity"],
            "source_network_denied": True,
            "physical_copy_distinct_inodes": True,
            "code_revision": code_revision,
            "source_reads_after_probes": 0,
            "network_calls_after_probes": 0,
        }
        for pid in [12345, 12346]
    ]
    manifest = monthly.seal_monthly(
        root,
        Budget(),
        expected_contract_digest=digest(raw_contract),
        code_revision=code_revision,
        independent_receipts=receipts,
        expected_negative_probes={
            "missing": "MONTHLY_ODS_PHYSICAL_FILE_SET_CHANGED",
            "corrupt": "MONTHLY_ODS_PHYSICAL_BYTES_CHANGED",
            "restored_all_original_file_hashes": "PASS",
        },
    )
    contract = {
        "schema_version": 1,
        "kind": "ods_monthly_warehouse_input_contract",
        "purpose": mod.INPUT_PURPOSE,
        "business_timezone": "Asia/Shanghai",
        "permitted_uses": [mod.INPUT_PURPOSE],
        "execution_authorized": False,
        "research_admission": "NOT_GRANTED",
        "revision_rule": "RETAIN_ORIGINAL_REVISIONS_NO_IMPLICIT_PRECEDENCE",
        "model_sha256": file_sha(MODEL),
        "image": {
            "bundle_id": root.name,
            "manifest_sha256": file_sha(root / "manifest.json"),
            "content_digest": manifest["content_digest"],
            "policy_sha256": digest(raw_contract),
            "scan_identity": scan["identity"],
            "through_date": "2026-09-30",
        },
    }
    cp = tmp_path / "consumer.json"
    cp.write_bytes(canonical(contract))
    return root, cp, sources


def open_input(image, **kwargs):
    root, contract, _ = image
    return mod.open_monthly_input(
        root=root,
        contract_path=contract,
        model_path=MODEL,
        budget=Budget(),
        knowledge_cutoff="2026-10-12T00:00:00Z",
        **kwargs,
    )


def test_model_maps_original_metadata_and_keeps_raw_values_and_unknown_clocks(image):
    before = file_sha(image[0] / "manifest.json")
    value = open_input(image)
    assert len(value.records) == 3
    seed = next(r for r in value.records if r["source_role"] == "historical_seed")
    assert seed["dataset_id"] == "daily_ohlcv_unadjusted"
    assert "knowledge_cutoff" not in seed  # No PIT invented from package time.
    event = next(r for r in value.records if r["source_role"] == "events")
    assert event["knowledge_cutoff"] == "2026-10-08T21:39:35+08:00"
    assert event["source_created_at"] == "2026-10-09T01:00:00Z"
    assert event["provider_acquisition_inferred"] is False
    row = pq.read_table(value.member(event["path"])).to_pylist()[0]
    assert row["precise"] == Decimal("0.12345678901")
    assert row["unknown"] is None and row["zero"] == 0
    assert row["acquired_at"] == "2026-10-08T21:39:35+08:00"
    assert value.identity["execution_authorized"] is False
    assert value.identity["scoped_source_read_admission"] == "PASSED"
    assert value.identity["scope_gaps"]
    assert file_sha(image[0] / "manifest.json") == before


@pytest.mark.parametrize("purpose", ["P6", "intraday_execution", "historical_PIT", ""])
def test_other_uses_rejected_before_native_scan(image, purpose, monkeypatch):
    monkeypatch.setattr(mod, "audit_monthly", lambda *a, **k: pytest.fail("unadmitted native read"))
    with pytest.raises(mod.MonthlyOdsInputError, match="USE_NOT_ADMITTED"):
        open_input(image, purpose=purpose)


@pytest.mark.parametrize(
    "field",
    [
        "manifest_sha256",
        "policy_sha256",
        "scan_identity",
        "content_digest",
        "through_date",
        "bundle_id",
    ],
)
def test_pinned_image_changes_fail_before_native_scan(image, field, monkeypatch):
    contract = json.loads(image[1].read_bytes())
    contract["image"][field] = (
        "sha256:" + "0" * 64
        if "sha256" in field or field in ("scan_identity", "content_digest")
        else "changed"
    )
    image[1].write_bytes(canonical(contract))
    monkeypatch.setattr(mod, "audit_monthly", lambda *a, **k: pytest.fail("unadmitted native read"))
    with pytest.raises(mod.MonthlyOdsInputError):
        open_input(image)


@pytest.mark.parametrize(
    "change",
    [
        {"execution_authorized": True},
        {"research_admission": "GRANTED"},
        {"permitted_uses": [mod.INPUT_PURPOSE, "historical_PIT"]},
        {"revision_rule": "LATEST_PACKAGE_WINS"},
        {"model_sha256": "sha256:" + "0" * 64},
    ],
)
def test_contract_does_not_silently_widen_admission(image, change):
    contract = json.loads(image[1].read_bytes())
    contract.update(change)
    image[1].write_bytes(canonical(contract))
    with pytest.raises(mod.MonthlyOdsInputError, match="CONTRACT_CHANGED"):
        open_input(image)


def test_business_month_end_does_not_allow_backdating_observation(image, monkeypatch):
    monkeypatch.setattr(mod, "audit_monthly", lambda *a, **k: pytest.fail("unadmitted native read"))
    with pytest.raises(mod.MonthlyOdsInputError, match="OBSERVATION_AFTER_CUTOFF"):
        mod.open_monthly_input(
            root=image[0],
            contract_path=image[1],
            model_path=MODEL,
            budget=Budget(),
            knowledge_cutoff="2026-09-30T23:59:59+08:00",
        )


@pytest.mark.parametrize("what", ["missing", "corrupt", "extra", "symlink", "hardlink"])
def test_physical_failures_never_fallback_to_original_sources(image, what):
    root = image[0]
    relative = "payload/history.parquet"
    member = root / relative
    if what == "missing":
        member.unlink()
    elif what == "corrupt":
        member.write_bytes(b"SYNTHETIC_CORRUPTION")
    elif what == "extra":
        (root / "unexpected").write_text("synthetic")
    elif what == "symlink":
        member.unlink()
        member.symlink_to(Path(image[2][0]["root"]) / relative)
    else:
        os.link(member, root.parent / "other-link")
    with pytest.raises((monthly.MonthlyOdsError, SourceAssessmentError, FileNotFoundError)):
        open_input(image)


def test_post_admission_member_corruption_and_undeclared_access_rejected(image):
    value = open_input(image)
    with pytest.raises(mod.MonthlyOdsInputError, match="UNDECLARED_MEMBER"):
        value.member("../original_source.parquet")
    member = value.member(value.records[0]["path"])
    member.write_bytes(b"synthetic corruption after admission")
    with pytest.raises(mod.MonthlyOdsInputError, match="MEMBER_CHANGED_AFTER_ADMISSION"):
        value.member(value.records[0]["path"])


def test_two_independent_new_processes_read_relocated_image_without_old_roots(image, tmp_path):
    root, contract, sources = image
    expected = open_input(image).identity
    for source in sources:
        shutil.rmtree(source["root"])
    code = """import json,os,socket,sys
from pathlib import Path
from quanthus.pipelines.ods_monthly_input import open_monthly_input
class B:
 def check(self): pass
 def begin_stage(self,name): pass
def deny(*a,**k): raise RuntimeError("SOURCE_NETWORK_DENIED")
socket.create_connection=deny
v=open_monthly_input(root=Path(sys.argv[1]),contract_path=Path(sys.argv[2]),model_path=Path(sys.argv[3]),budget=B(),knowledge_cutoff="2026-10-12T00:00:00Z")
for r in v.records:
 if r['record_type']=='file': v.member(r['path'])
print(json.dumps({'pid':os.getpid(),'identity':v.identity}))
"""
    pids = []
    for i in range(2):
        relocated = tmp_path / f"move{i}"
        shutil.copytree(root, relocated)
        assert (root / "manifest.json").stat().st_ino != (relocated / "manifest.json").stat().st_ino
        shutil.rmtree(root)
        result = subprocess.run(
            [sys.executable, "-c", code, str(relocated), str(contract), str(MODEL)],
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(MODEL.parents[2] / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        evidence = json.loads(result.stdout)
        assert evidence["identity"] == expected
        pids.append(evidence["pid"])
        root = relocated
    assert len(set(pids)) == 2


def test_monthly_planner_is_opt_in_and_never_relabels_july_plan(image, tmp_path):
    root, contract, _ = image
    args = dict(
        ods_package=root,
        model_path=MODEL,
        output=tmp_path / "plan",
        run_id="synthetic",
        knowledge_cutoff="2026-10-12T00:00:00Z",
        code_revision="a" * 40,
        runtime_id="synthetic",
        through_date="2026-09-30",
        monthly_consumer_contract=contract,
        monthly_budget=Budget(),
    )
    with pytest.raises(WarehousePlanningError, match="explicit monthly_base"):
        build_input_plan(mode="base", **args)
    assert not (tmp_path / "plan").exists()
    result = build_input_plan(mode="monthly_base", **args)
    plan = json.loads((result.root / "input-plan.json").read_bytes())["plan"]
    assert plan["mode"] == "monthly_base" and plan["through_date"] == "2026-09-30"
    assert plan["input"]["kind"] == "ods_monthly_warehouse_input"
    assert plan["source_precedence"]["implicit_source_priority"] is False
    assert plan["input"]["execution_authorized"] is False
    assert build_input_plan(mode="monthly_base", **args).plan_digest == result.plan_digest


def test_planner_rejects_overlap_without_source_mutation(image):
    before = file_sha(image[0] / "manifest.json")
    with pytest.raises(SourceAssessmentError, match="SOURCE_OUTPUT_OVERLAP"):
        build_input_plan(
            ods_package=image[0],
            model_path=MODEL,
            output=image[0] / "bad-output",
            mode="monthly_base",
            run_id="synthetic",
            knowledge_cutoff="2026-10-12T00:00:00Z",
            code_revision="a" * 40,
            runtime_id="synthetic",
            monthly_consumer_contract=image[1],
            monthly_budget=Budget(),
        )
    assert not (image[0] / "bad-output").exists()
    assert file_sha(image[0] / "manifest.json") == before


@pytest.mark.parametrize("image", ["unknown_nonempty"], indirect=True)
def test_nonempty_unknown_artifact_blocks_before_expensive_scan(image, monkeypatch):
    monkeypatch.setattr(
        mod, "audit_monthly", lambda *a, **k: pytest.fail("unclassified native read")
    )
    with pytest.raises(mod.MonthlyOdsInputError, match="UNCLASSIFIED_ARTIFACT"):
        open_input(image)


@pytest.mark.parametrize("image", ["two_revisions"], indirect=True)
def test_two_original_revisions_are_retained_without_package_time_priority(image):
    value = open_input(image)
    events = [r for r in value.records if r["source_role"] == "events"]
    assert len(events) == 2 and len({r["source_id"] for r in events}) == 2
    assert len({r["path"] for r in events}) == 2
    assert value.identity["revision_rule"] == "RETAIN_ORIGINAL_REVISIONS_NO_IMPLICIT_PRECEDENCE"
    for event in events:
        value.member(event["path"])


@pytest.mark.parametrize("change", ["grant", "execution", "same_pid", "network", "negative"])
def test_repacked_flags_or_missing_independence_do_not_grant_use(image, change, monkeypatch):
    root, cp, _ = image
    m = json.loads((root / "manifest.json").read_bytes())
    c = m["content"]
    if change == "grant":
        c["consumer_admission"] = "GRANTED"
    elif change == "execution":
        c["authority"]["execution_authorized"] = True
    elif change == "same_pid":
        c["independent_acceptance"][1]["pid"] = c["independent_acceptance"][0]["pid"]
    elif change == "network":
        c["independent_acceptance"][0]["network_calls_after_probes"] = 1
    else:
        c["negative_probes"] = {}
    m["content_digest"] = digest(c)
    (root / "manifest.json").write_bytes(canonical(m))
    (root / "READY").write_text(file_sha(root / "manifest.json") + "\n")
    contract = json.loads(cp.read_bytes())
    contract["image"].update(
        manifest_sha256=file_sha(root / "manifest.json"), content_digest=m["content_digest"]
    )
    cp.write_bytes(canonical(contract))
    monkeypatch.setattr(mod, "audit_monthly", lambda *a, **k: pytest.fail("invalid native read"))
    with pytest.raises(mod.MonthlyOdsInputError):
        open_input(image)


def test_verification_cli_accepts_successor_and_keeps_legacy_options_explicit(image, tmp_path):
    ops = MODEL.parents[2] / "ops/bin/verify-ods-base"
    args = [
        sys.executable,
        str(ops),
        "--bundle",
        str(image[0]),
        "--monthly-consumer-contract",
        str(image[1]),
        "--monthly-audit-root",
        str(tmp_path / "audit"),
        "--model",
        str(MODEL),
        "--knowledge-cutoff",
        "2026-10-12T00:00:00Z",
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    identity = json.loads(result.stdout)
    assert identity["scoped_source_read_admission"] == "PASSED"
    assert identity["execution_authorized"] is False
    assert identity["through_date"] == "2026-09-30"
    mixed = subprocess.run([*args, "--policy", str(MODEL)], capture_output=True, text=True)
    assert mixed.returncode == 2
    assert file_sha(image[0] / "manifest.json") == identity["manifest_sha256"]
