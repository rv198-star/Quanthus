"""Fixed historical control boundaries without opening any market source."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanthus.expert_strategy import p3_historical_controls as c

R = Path(__file__).resolve().parents[2]


@pytest.fixture
def role_freeze(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe.txt").write_text("synthetic input remains unchanged")
    root = tmp_path / "job"
    root.mkdir()
    work = root / "primary"
    work.mkdir()
    freeze = {
        "repository": str(R),
        "roles": {
            "primary": {
                "work": str(work),
                **{
                    k: str(source)
                    for k in (
                        "source",
                        "parent_source",
                        "prepared",
                        "prior_prepared",
                        "reference_work",
                    )
                },
                "admission": str(source / "safe.txt"),
                "bindings": {},
            }
        },
        "deadline_UTC": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "maximum_new_owned_logical_bytes": 1024,
        "minimum_data_free_bytes": 0,
        "job_output": str(root),
        "same_UTC_snapshot": {"data_device": root.stat().st_dev},
    }
    return freeze, source, work


@pytest.mark.parametrize(
    "case", ["equal", "within_source", "contains_source", "symlink", "expired"]
)
def test_role_preflight_rejects_before_any_source_or_output_write(role_freeze, case):
    freeze, source, work = role_freeze
    if case == "equal":
        freeze["roles"]["primary"]["work"] = str(source)
    elif case == "within_source":
        freeze["roles"]["primary"]["work"] = str(source / "nested")
    elif case == "contains_source":
        freeze["roles"]["primary"]["work"] = str(source.parent)
    elif case == "symlink":
        link = work.parent / "alias"
        link.symlink_to(source, target_is_directory=True)
        freeze["roles"]["primary"]["work"] = str(link)
    else:
        freeze["deadline_UTC"] = "2000-01-01T00:00:00+00:00"
    before = {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    paths = set(source.rglob("*"))
    with pytest.raises(ValueError, match="CONTROL_(PATH|DEADLINE|OUTPUT)"):
        c.run_role(freeze, "primary")
    assert paths == set(source.rglob("*"))
    assert before == {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert not list(work.iterdir())


def test_role_preflight_keeps_healthy_readonly_prerequisite_reuse(role_freeze):
    freeze, source, work = role_freeze
    (work / "temp").mkdir()
    (work / "cache").mkdir()
    assert c.preflight_role(freeze, "primary")["data_device"] == source.stat().st_dev
    assert not (work / "replays").exists()


@pytest.mark.parametrize(
    "damage", ["temp_alias", "cross_role", "device", "space", "existing_output"]
)
def test_role_entry_checks_tmp_other_roles_and_frozen_capacity(role_freeze, damage):
    freeze, source, work = role_freeze
    if damage == "temp_alias":
        (work / "temp").symlink_to(source, target_is_directory=True)
    elif damage == "cross_role":
        freeze["roles"]["independent_cold_reproduction"] = copy.deepcopy(freeze["roles"]["primary"])
    elif damage == "device":
        freeze["same_UTC_snapshot"]["data_device"] = -1
    elif damage == "space":
        freeze["maximum_new_owned_logical_bytes"] = 10**30
    else:
        (work / "replays").mkdir()
    before = (source / "safe.txt").read_bytes()
    with pytest.raises(ValueError, match="CONTROL_"):
        c.run_role(freeze, "primary")
    assert (source / "safe.txt").read_bytes() == before
    assert not (work / "isolation.json").exists()


def test_exact_parent_and_new_component_request():
    request = c.load_request(R)
    assert request["source_manifest_sha256"] == c.parent.load_request(R)["source_manifest_sha256"]
    assert request["components"] == c.COMPONENTS
    assert request["optional_paid_rights"] == "DO_NOT_SUBSCRIBE"
    assert request["scope"] == ["2012-01-04", "2021-12-30", "2021-12-31"]
    assert c.FixedControlRuntime.run_variant is c.parent.RestructuringRuntime.run_variant


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_manifest_sha256", "sha256:" + "0" * 64),
        ("parent_request_digest", "sha256:" + "0" * 64),
        ("entry_cap", "0.25"),
        ("optional_paid_rights", "SUBSCRIBE"),
        ("historical_PIT_claim", True),
        ("new_source_collection", True),
        ("source_guards_waived", True),
        ("reuse", "PRIMARY_FOR_BOTH"),
        ("components", {"TC": {"entry_cap": True, "held_risk": True}}),
    ],
)
def test_repacked_control_cannot_relax_frozen_semantics(monkeypatch, field, value):
    request = copy.deepcopy(c.load_request(R))
    request[field] = value
    monkeypatch.setattr(c, "read_json", lambda *a, **kw: (request, "ignored"))
    with pytest.raises(ValueError, match="CONTROL_REQUEST"):
        c.load_request(R)


@pytest.mark.parametrize("arm,entry,held", [("TC", "TCH", False), ("TH", "T", True)])
def test_components_dispatch_to_exact_original_entry_and_held_policy(monkeypatch, arm, entry, held):
    runtime = c.FixedControlRuntime.__new__(c.FixedControlRuntime)
    calls = []
    monkeypatch.setattr(
        c.parent.RestructuringRuntime,
        "_evaluate_entry_decision",
        lambda self, runner, **kw: calls.append(kw) or "same-decision",
    )
    assert (
        runtime._evaluate_entry_decision(None, intervention_id=arm, sentinel=17) == "same-decision"
    )
    assert calls == [{"intervention_id": entry, "sentinel": 17}]
    assert (runtime._held_intervention_id(arm) is None) == held
    for forbidden in ("B", "T", "TCH", None, "TC2"):
        with pytest.raises(ValueError, match="CONTROL_ARM"):
            runtime._held_intervention_id(forbidden)


def test_bound_input_change_link_and_extra_path_are_not_reused(tmp_path):
    source = tmp_path / "input.json"
    raw = b'{"safe": true}\n'
    source.write_bytes(raw)
    bindings = {
        str(source): {"bytes": len(raw), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
    }
    proof = c.verify_bindings(bindings)
    assert proof[str(source)]["inode"] == source.stat().st_ino
    source.write_bytes(raw.replace(b"true", b"null"))
    with pytest.raises(ValueError, match="CONTROL_INPUT_CHANGED"):
        c.verify_bindings(bindings)
    source.write_bytes(raw)
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="CONTROL_INPUT_SYMLINK"):
        c.verify_bindings({str(link): bindings[str(source)]})
    os.link(source, tmp_path / "hardlink")
    with pytest.raises(ValueError, match="CONTROL_INPUT_PHYSICAL"):
        c.verify_bindings(bindings)


def test_kernel_isolation_rejects_native_other_role_reads_and_network(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "safe.txt"
    inside.write_text("inside")
    outside = tmp_path / "other-role.txt"
    outside.write_text("outside")
    code = """
import json,sys
from pathlib import Path
from quanthus.expert_strategy.p3_historical_controls import isolate_role
system=['/usr','/lib','/lib64','/etc','/dev','/proc','/sys']
paths=[Path(sys.argv[1]),Path(sys.prefix)]
paths += [Path(p).resolve() for p in system if Path(p).exists()]
result=isolate_role(paths,[Path(sys.argv[2])])
import pyarrow as pa
with pa.OSFile(sys.argv[3],'r') as f:assert f.read()==b'inside'
print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(allowed), str(outside), str(inside)],
        env={**os.environ, "PYTHONPATH": str(R / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof["kernel_landlock"] is proof["seccomp_network_denied"] is True
    assert proof["native_denied"] == [str(outside)]
    assert outside.read_text() == "outside"
