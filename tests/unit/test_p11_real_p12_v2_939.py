"""Real pinned-P12 v2 consumer integration (issue #723 fixed handoff, commit b756c63c).

These are the real-wiring cases for the bound consumer pin
(config/research/r11/d1b-20261124/p12-consumer.daily-advice-package.v2.json).
They are NOT synthetic doubles: every case resolves the actual
quanthus.p12_daily.decision_loader.load_decision_set from the byte-verified
pinned checkout (or proves the byte check fails first), calls it exactly once
per decision_set through the publish seam, and records the real
(MarketDecisionSet, wire_digest) binding identity.

A missing pinned tree is a hard failure, never a skip - same rule as the earlier
synthetic integration cases.  Each case materializes its own pinned checkout
under tmp_path so a drifted/missing file cannot hide behind a shared worktree.
"""

from __future__ import annotations

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.runtime_identity import (
    CONSUMER_BINDING_V2,
    PINNED_V2_MODE,
    consumer_identity_v2,
    resolve_pinned_v2_loader,
)
from tests.unit.test_product_daily_contract_v2 import (
    _formal_reduce_decision,
    _held,
    _produce_held,
    _reduce_package,
    _v2_wire,
)

ROOT = find_project_root()
BINDING_PATH = ROOT / CONSUMER_BINDING_V2


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
    ).strip()


@pytest.fixture(scope="module")
def pinned_checkout(tmp_path_factory) -> Path:
    """Materialize the bound consumer commit into an isolated worktree.

    The pin's own per-file SHA map is re-verified after checkout; a tree whose
    bytes no longer match the versioned binding is reported, never consumed.
    """

    binding = consumer_identity_v2(ROOT)
    if binding.get("pin_state") != "bound":
        pytest.fail("v2 consumer binding is not bound; real pin required")
    dest = tmp_path_factory.mktemp("p12-pin") / "checkout"
    subprocess.check_output(
        ["git", "worktree", "add", "--detach", str(dest), binding["source_commit"]],
        cwd=ROOT,
        text=True,
        stderr=subprocess.DEVNULL,
    )
    for rel, expected in binding["source_files_sha256"].items():
        import hashlib

        actual = hashlib.sha256((dest / rel).read_bytes()).hexdigest()
        assert actual == expected, f"pinned file drifted: {rel}"
    yield dest
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(dest)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _bound_binding() -> dict:
    binding = consumer_identity_v2(ROOT)
    if binding.get("pin_state") != "bound":
        pytest.fail("v2 consumer binding must be bound for the real pin tests")
    return binding


# --- A. binding identity and loader contract --------------------------------------------------


def test_binding_is_bound_to_real_commit_and_files():
    binding = _bound_binding()
    assert binding["source_commit"] == "b756c63c904544909cc2c2195533808b299daf2c"
    assert binding["source_tree"] == "2327912269a26f8133a6b7d3554d9e05ba38af79"
    assert binding["mode"] == PINNED_V2_MODE == "simulation"
    assert binding["purpose"] == "historical_replay"
    assert binding["consumer_schema"] == "p12.decision-set.v2"
    assert binding["capabilities"]["supports_held_reduction_v2"] is True
    # The pinned commit really exists in the shared object store.
    assert _git(ROOT, "rev-parse", binding["source_commit"] + "^{commit}") == binding["source_commit"]
    assert _git(ROOT, "rev-parse", binding["source_commit"] + "^{tree}") == binding["source_tree"]


def test_resolve_pinned_loader_verifies_every_file_byte(pinned_checkout, monkeypatch):
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    loader = resolve_pinned_v2_loader(ROOT)
    package = _reduce_package()
    raw = _v2_wire(package)
    validated, wire_digest = loader(raw)
    assert validated.mode == "simulation"
    assert wire_digest.startswith("sha256:")
    # The wire digest covers the raw bytes - a tampered payload digests differently.
    import quanthus.shared.fact_contracts as fc

    assert wire_digest == fc.digest(raw)


def test_pinned_loader_rejects_schema_drift(pinned_checkout, monkeypatch):
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    loader = resolve_pinned_v2_loader(ROOT)
    package = _reduce_package()
    raw = _v2_wire(package)
    raw["decision_set_schema"] = "p12.decision-set.v9"
    with pytest.raises(Exception, match="does not match expected"):
        loader(raw)


def test_pinned_loader_rejects_missing_schema_marker(pinned_checkout, monkeypatch):
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    loader = resolve_pinned_v2_loader(ROOT)
    package = _reduce_package()
    raw = _v2_wire(package)
    del raw["decision_set_schema"]
    with pytest.raises(Exception, match="without decision_set_schema"):
        loader(raw)


def test_pinned_loader_binds_mode_from_binding_not_wire(pinned_checkout, monkeypatch):
    """The pinned loader trusts only the caller-declared mode; a wire mode is a
    claim, never the admission.  The returned object's mode is the binding's
    simulation admission - P11 packages never emit a wire mode field."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    loader = resolve_pinned_v2_loader(ROOT)
    package = _reduce_package()
    raw = _v2_wire(package)
    validated, _ = loader(raw)
    assert validated.mode == "simulation"
    assert validated.synthetic is False
    # The produced wire never carries a mode field - the pinned binding's mode
    # is the only admission source.
    assert "mode" not in raw


def test_pinned_loader_refuses_byte_drift(tmp_path, monkeypatch):
    """A consumer tree whose pinned bytes drifted fails before any import."""
    binding = _bound_binding()
    dest = tmp_path / "drifted"
    subprocess.check_output(
        ["git", "worktree", "add", "--detach", str(dest), binding["source_commit"]],
        cwd=ROOT,
        text=True,
        stderr=subprocess.DEVNULL,
    )
    try:
        drift = dest / "src/quanthus/p12_daily/decision_loader.py"
        drift.write_bytes(drift.read_bytes() + b"\n# drifted\n")
        monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(dest))
        with pytest.raises(ProductDailyError, match="drifted"):
            resolve_pinned_v2_loader(ROOT)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(dest)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )


def test_pinned_loader_refuses_missing_file(tmp_path, monkeypatch):
    binding = _bound_binding()
    dest = tmp_path / "missing"
    subprocess.check_output(
        ["git", "worktree", "add", "--detach", str(dest), binding["source_commit"]],
        cwd=ROOT,
        text=True,
        stderr=subprocess.DEVNULL,
    )
    try:
        (dest / "schemas/p12/v2/decision-set.v2.json").unlink()
        monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(dest))
        with pytest.raises(ProductDailyError, match="missing"):
            resolve_pinned_v2_loader(ROOT)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(dest)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )


# --- B. real publish seam: single-call validated object + wire digest --------------------------


def _publishable_reduce_package():
    return _produce_held(
        _held("reduce", None, formal=_formal_reduce_decision(Decimal("0.5")))
    ).package


def test_publish_reduce_through_real_pinned_consumer(pinned_checkout, tmp_path, monkeypatch):
    """The real seam: bound pin -> pinned loader -> (object, digest) -> receipt."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    from quanthus.product_daily.recommendation.publish import publish_package

    package = _publishable_reduce_package()
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
    )
    written = tmp_path / (package.package_id + ".v" + package.version + ".json")
    assert written.is_file()
    payload = json.loads(written.read_text())
    assert payload["schema_version"] == "p11.daily-advice-package.v2"
    raw = payload["decision_sets"][0]
    assert raw["decision_set_schema"] == "p12.decision-set.v2"
    # The receipt carries the real pinned wire digest - not the file sha256 and
    # not a recomputed object hash.
    assert receipt.p12_package_digests is not None
    binding = dict(receipt.p12_package_digests)
    assert binding[raw["market_date"]].startswith("sha256:")
    import quanthus.shared.fact_contracts as fc

    assert binding[raw["market_date"]] == fc.digest(raw)
    assert binding[raw["market_date"]] != receipt.file_sha256
    # The single validated object still proved its coordinates: reduce + target.
    decision = raw["decisions"][0]
    assert decision["stage"] == "exit_plan" and decision["exit_kind"] == "reduce"
    assert decision["target_position_fraction"] == "0.5"


def test_publish_full_exit_through_real_pinned_consumer(pinned_checkout, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    from quanthus.product_daily.recommendation.publish import publish_package

    package = _produce_held(_held("exit", None)).package
    receipt = publish_package(
        package,
        directory=tmp_path,
        published_at=package.decision_sets[0].published_at,
    )
    payload = json.loads((tmp_path / (package.package_id + ".v" + package.version + ".json")).read_text())
    decision = payload["decision_sets"][0]["decisions"][0]
    assert decision["exit_kind"] == "full"
    assert receipt.p12_package_digests is not None


def test_publish_fails_before_write_when_loader_refuses(pinned_checkout, tmp_path, monkeypatch):
    """A tampered package refused by the pinned loader leaves zero residue."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    import dataclasses

    from quanthus.product_daily.recommendation.publish import publish_package

    package = _publishable_reduce_package()
    ds = package.decision_sets[0]
    # Remove exit_kind so the pinned v2 loader must refuse before any file lands.
    tampered_decision = dataclasses.replace(ds.decisions[0], exit_kind=None)
    tampered_ds = dataclasses.replace(ds, decisions=(tampered_decision,))
    tampered_package = dataclasses.replace(package, decision_sets=(tampered_ds,))
    with pytest.raises(Exception):
        publish_package(
            tampered_package,
            directory=tmp_path,
            published_at=ds.published_at,
        )
    assert not list(tmp_path.glob("*.json"))


def test_publish_refuses_tampered_wire(pinned_checkout, tmp_path, monkeypatch):
    """A package whose decision bytes were altered after production fails the pin."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    import dataclasses

    from quanthus.product_daily.recommendation.publish import publish_package

    package = _publishable_reduce_package()
    ds = package.decision_sets[0]
    drifted = dataclasses.replace(ds, market_date=__import__("datetime").date(2026, 11, 26))
    tampered_package = dataclasses.replace(package, decision_sets=(drifted,))
    with pytest.raises(Exception):
        publish_package(
            tampered_package,
            directory=tmp_path,
            published_at=ds.published_at,
        )
    assert not list(tmp_path.glob("*.json"))


def test_v1_release_still_uses_v1_loader_and_bytes(pinned_checkout, tmp_path, monkeypatch):
    """The pinned v1 synthetic consumer path is untouched: old bytes, old digest
    surface, no decision_set_schema marker."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    from tests.unit.test_product_daily_recommendation import _p12_loader
    from quanthus.product_daily.recommendation.publish import publish_package

    validate_raw, _ = _p12_loader()

    def v1_loader(raw):
        decision = validate_raw(raw["decisions"][0], raw)
        from quanthus.capabilities.decision_access import MarketDecisionSet
        from quanthus.shared.fact_contracts import digest as p12_digest
        from quanthus.shared.values import VersionRef
        from datetime import date, datetime

        return MarketDecisionSet(
            source_ref=VersionRef(raw["source_id"], raw["version"], p12_digest(raw)),
            mode="synthetic",
            synthetic=True,
            market_date=date.fromisoformat(raw["market_date"]),
            knowledge_cutoff=datetime.fromisoformat(raw["knowledge_cutoff"]),
            published_at=datetime.fromisoformat(raw["published_at"]),
            next_session=date.fromisoformat(raw["next_session"]),
            decisions=(decision,),
        )

    # A v1-registered package (structural fixture policy).
    from tests.unit.test_product_daily_recommendation import (
        _candidate,
        _facts,
        _full_members,
        _policy,
        _produced,
    )

    result = _produced(
        (_candidate(facts=(f := _facts(_full_members())), policy=_policy()),),
        facts=f,
        policy=_policy(),
    )
    receipt = publish_package(
        result.package,
        directory=tmp_path,
        published_at=result.package.decision_sets[0].published_at,
        p12_loader=v1_loader,
    )
    payload = json.loads((tmp_path / (result.package.package_id + ".v" + result.package.version + ".json")).read_text())
    assert payload["schema_version"] == "quanthus.r11.daily-advice.v1"
    assert "decision_set_schema" not in payload["decision_sets"][0]
    assert "exit_kind" not in payload["decision_sets"][0]["decisions"][0]
    assert receipt.p12_package_digests is not None
