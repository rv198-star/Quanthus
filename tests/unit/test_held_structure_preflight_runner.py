"""Offline file-boundary regressions for the #545 census command."""

import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from quanthus.expert_strategy.held_structure_preflight import PreflightError
from scripts import expert545_preflight as driver
from tests.unit.test_held_structure_preflight import archive


@pytest.fixture
def selected(tmp_path, monkeypatch):
    monkeypatch.setattr(driver.shutil, "disk_usage", lambda _: SimpleNamespace(free=1 << 40))

    def no_network(*args, **kwargs):
        raise AssertionError("offline preflight attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    source = tmp_path / "source"
    members, events = [], []

    def add(path, value, *, event=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value).encode()
        path.write_bytes(payload)
        (events if event else members).append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )

    for arm in ("B", "BH", "TH"):
        decisions, snapshots, coordinates = archive()
        root = source / f"variant.{arm}"
        add(root / "decisions.json", decisions)
        add(root / "engine-events.json", snapshots, event=True)
        add(root / "manifest.json", {"kind": "synthetic_manifest"})
    add(source / "event-coordinates.json", coordinates)
    add(
        source / "facts-factor.private.json",
        [
            {
                "security_code": "BBB",
                "market_date": "2021-01-05",
                "resistance_margin20": "0.02",
            }
        ],
    )
    selection = tmp_path / "inputs.json"
    selection.write_text(json.dumps({"members": members}))
    addition = tmp_path / "events.json"
    addition.write_text(
        json.dumps(
            {
                "parent_selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
                "members": events,
            }
        )
    )
    return selection, addition, tmp_path / "output", members + events


def test_command_reproduces_safe_scope_stop_without_network_or_source_changes(selected):
    selection, addition, output, members = selected
    receipt = driver.run(selection, addition, output)
    report = json.loads((output / "decision-footprint.safe.json").read_text())
    assert receipt["comparison"] == "NOT_RUN"
    assert report["verified_selected_members"] == len(members)
    assert [r["held_decisions"] for r in report["populations"]] == [1, 1, 1]
    assert report["potential_changed_decisions"] is None
    assert report["measured_effect_or_economic_value"] is None
    assert report["shared_intermediate"]["materializations"] == 1
    assert "quantity" not in (output / "decision-footprint.safe.json").read_text().replace(
        "same_episode_different_quantity", ""
    )
    for member in members:
        assert hashlib.sha256(Path(member["path"]).read_bytes()).hexdigest() == member["sha256"]
    with pytest.raises(PreflightError, match="output_already_exists"):
        driver.run(selection, addition, output)


def test_unused_manifest_corruption_also_fails_before_output_creation(selected):
    selection, addition, output, members = selected
    manifest = next(m for m in members if m["path"].endswith("variant.B/manifest.json"))
    Path(manifest["path"]).write_text("{}")
    with pytest.raises(PreflightError, match="input_sha256_mismatch"):
        driver.run(selection, addition, output)
    assert not output.exists()


def test_event_selection_cannot_rebind_another_parent_selection(selected):
    selection, addition, output, _ = selected
    payload = json.loads(addition.read_text())
    payload["parent_selection_sha256"] = "0" * 64
    addition.write_text(json.dumps(payload))
    with pytest.raises(PreflightError, match="selection_parent_mismatch"):
        driver.run(selection, addition, output)
    assert not output.exists()


def test_output_may_not_be_inside_any_selected_source_directory(selected):
    selection, addition, _, members = selected
    output = Path(members[0]["path"]).parent / "new-output"
    with pytest.raises(PreflightError, match="output_inside_selected_source"):
        driver.run(selection, addition, output)
    assert not output.exists()


def test_compatible_code_date_is_only_a_screen_and_never_auto_activates_comparison(selected):
    selection, addition, output, _ = selected
    payload = json.loads(selection.read_text())
    member = next(m for m in payload["members"] if m["path"].endswith("facts-factor.private.json"))
    data = json.dumps(
        [
            {
                "security_code": "AAA",
                "market_date": "2021-01-05",
                "unreclaimed_age_sessions": 5,
                "signed_distance_to_support": "-0.02",
                "post_break_rebound": "0.05",
            }
        ]
    ).encode()
    Path(member["path"]).write_bytes(data)
    member.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    selection.write_text(json.dumps(payload))
    child = json.loads(addition.read_text())
    child["parent_selection_sha256"] = hashlib.sha256(selection.read_bytes()).hexdigest()
    addition.write_text(json.dumps(child))
    driver.run(selection, addition, output)
    report = json.loads((output / "decision-footprint.safe.json").read_text())
    assert report["factor_fixture_screens"][0]["coarse_code_date_overlap"] == 1
    assert report["factor_fixture_screens"][0]["missing_support_fields"] == []
    assert "NO_COARSE_DECISION_FACT_OVERLAP" not in report["blockers"]
    assert "PRICE_BASIS_BRIDGE_QUALIFIED_MISSING" in report["blockers"]
    assert report["comparison"] == "NOT_RUN"
