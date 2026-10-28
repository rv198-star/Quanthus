"""Synthetic checks of the commitment boundary; no market/reference readers."""

import base64
import hashlib
import importlib
import json
import zlib
from pathlib import Path

import pytest


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("entry_march_fit")


def fixture(job):
    (job / "reference").mkdir()
    (job / "evidence").mkdir()
    rubric = job / "rubric.json"
    rubric.write_text("{}")
    (job / "inputs.frozen.private.json").write_text(json.dumps({"spec": {"rubric": str(rubric)}}))
    packet = job / "reference/packet.json"
    packet.write_text("{}")
    rows = [{"blind_id": str(i), "opinion": "UNKNOWN"} for i in range(87)]
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return {
        "reader": "A",
        "codec": "zlib+base64",
        "count": 87,
        "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
        "rubric_sha256": hashlib.sha256(rubric.read_bytes()).hexdigest(),
        "canonical_uncompressed_sha256": hashlib.sha256(raw).hexdigest(),
        "payload": base64.b64encode(zlib.compress(raw)).decode(),
    }, rows


def test_panel_commitment_binds_exact_payload_packet_and_rubric(cli, tmp_path):
    commit, rows = fixture(tmp_path)
    path = tmp_path / "reference/commit-A.json"
    path.write_text(json.dumps(commit))
    assert cli.committed_panel(tmp_path, "A")[0] == rows
    commit["canonical_uncompressed_sha256"] = "0" * 64
    path.write_text(json.dumps(commit))
    with pytest.raises(ValueError, match="COMMITMENT_BYTES"):
        cli.committed_panel(tmp_path, "A")


def test_changed_packet_cannot_reuse_opinions(cli, tmp_path):
    commit, _ = fixture(tmp_path)
    (tmp_path / "reference/commit-A.json").write_text(json.dumps(commit))
    (tmp_path / "reference/packet.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="COMMITMENT_BINDING"):
        cli.committed_panel(tmp_path, "A")


def test_failed_support_prevents_fit_and_input_access(cli, tmp_path, monkeypatch):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/reference-support.safe.json").write_text(
        '{"gate":"STOP_FIXED_BATCH","failures":["class_support"]}'
    )
    monkeypatch.setattr(cli, "guard", lambda _: None)

    def prohibited(*args, **kwargs):
        raise AssertionError("must not read model/source inputs or fit")

    monkeypatch.setattr(cli, "fit_head", prohibited)
    monkeypatch.setattr(cli, "verify", prohibited)
    with pytest.raises(ValueError, match="NO_FIT"):
        cli.fit(tmp_path)
    assert not (tmp_path / "private/checkpoint.json").exists()


def test_corrupt_prediction_commitment_blocks_opinion_opening(cli, tmp_path, monkeypatch):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "private").mkdir()
    (tmp_path / "private/checkpoint.json").write_text("{}")
    (tmp_path / "evidence/prediction-commitment.safe.json").write_text(
        json.dumps({"checkpoint_sha256": "0" * 64})
    )
    monkeypatch.setattr(cli, "guard", lambda _: None)

    def prohibited(*args, **kwargs):
        raise AssertionError("must not open evaluation opinions")

    monkeypatch.setattr(cli, "committed_panel", prohibited)
    with pytest.raises(ValueError, match="PREDICTION_COMMITMENT_CHANGED"):
        cli.quality(tmp_path)
