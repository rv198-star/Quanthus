"""Real pinned-P12 v2 consumer integration (issue #723 fixed handoff, commit b756c63c).

These are the real-wiring cases for the bound consumer pin
(config/research/r11/d1b-20261124/p12-consumer.daily-advice-package.v2.json).
They are NOT synthetic doubles: every case resolves the actual
quanthus.p12_daily.decision_loader.load_decision_set from the byte-verified
pinned checkout (or proves the byte check fails first), calls it exactly once
per decision_set through the publish seam, and records the real
(MarketDecisionSet, wire_digest) tuple.  The receipt keeps the two digest
surfaces separate: p12_package_digests is the account binding identity the P12
ConfigurationBinding stores (digest(_package_content(validated)) over the same
validated object), p12_wire_digests is the loader's raw-byte identity.

A missing pinned tree is a hard failure, never a skip - same rule as the earlier
synthetic integration cases.  Each case materializes its own pinned checkout
under tmp_path so a drifted/missing file cannot hide behind a shared worktree.
"""

from __future__ import annotations

import json
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


_PINNED_PREFIXES = ("quanthus.shared", "quanthus.capabilities", "quanthus.p12_daily")


def _evict_pinned() -> None:
    """Drop pinned-tree modules the host must never retain between probes."""
    import sys as _sys

    for _m in list(_sys.modules):
        if _m.startswith(_PINNED_PREFIXES):
            del _sys.modules[_m]


def _written_payload(directory: Path, package) -> dict:
    written = directory / (package.package_id + ".v" + package.version + ".json")
    return json.loads(written.read_text())


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
    assert (
        _git(ROOT, "rev-parse", binding["source_commit"] + "^{commit}")
        == (binding["source_commit"])
    )
    assert _git(ROOT, "rev-parse", binding["source_commit"] + "^{tree}") == binding["source_tree"]


def test_resolve_pinned_loader_verifies_every_file_byte(pinned_checkout, monkeypatch):
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    loader = resolve_pinned_v2_loader(ROOT)
    package = _reduce_package()
    raw = _v2_wire(package)
    validated, wire_digest, account_digest = loader(raw)
    assert validated.mode == "simulation"
    assert wire_digest.startswith("sha256:")
    assert account_digest.startswith("sha256:")
    # The wire digest covers the raw bytes, the account digest covers the
    # validated object's package content - both recomputed here through the
    # pinned tree's own fact_contracts.
    import dataclasses as _dc

    import quanthus as _q

    _q.__path__.insert(0, str(pinned_checkout / "src" / "quanthus"))
    try:
        import quanthus.shared.fact_contracts as fc

        assert wire_digest == fc.digest(raw)
        account_content = json.loads(json.dumps(_dc.asdict(validated), default=str, sort_keys=True))
        assert account_digest == fc.digest(account_content)
        assert account_digest != wire_digest
    finally:
        _q.__path__.remove(str(pinned_checkout / "src" / "quanthus"))
        _evict_pinned()


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
    validated, _, _ = loader(raw)
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
    # The receipt carries both real surfaces, kept separate: the account binding
    # digest (what the P12 ConfigurationBinding stores) and the pinned loader's
    # raw-byte wire digest - never the file sha256.
    assert receipt.p12_package_digests is not None
    assert receipt.p12_wire_digests is not None
    binding = dict(receipt.p12_package_digests)
    wires = dict(receipt.p12_wire_digests)
    assert binding[raw["market_date"]].startswith("sha256:")
    assert wires[raw["market_date"]].startswith("sha256:")
    import dataclasses as _dc

    import quanthus as _q

    _q.__path__.insert(0, str(pinned_checkout / "src" / "quanthus"))
    try:
        import quanthus.p12_daily.decision_loader as pinned_dl
        import quanthus.shared.fact_contracts as fc

        # Same single-call contract: the loader returns (validated, wire_digest),
        # the account digest is digest(_package_content(validated)) exactly as
        # quanthus.product_daily.application.desktop.service computes it.
        validated, wire = pinned_dl.load_decision_set(raw, expected_schema="p12.decision-set.v2")
        assert wires[raw["market_date"]] == wire == fc.digest(raw)
        account_content = json.loads(json.dumps(_dc.asdict(validated), default=str, sort_keys=True))
        assert binding[raw["market_date"]] == fc.digest(account_content)
    finally:
        _q.__path__.remove(str(pinned_checkout / "src" / "quanthus"))
        _evict_pinned()
    # The account binding digest is a different identity from the wire bytes and
    # from the artifact sha256; the fixture oracle records the exact pair.
    assert binding[raw["market_date"]] != wires[raw["market_date"]]
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
    payload = _written_payload(tmp_path, package)
    decision = payload["decision_sets"][0]["decisions"][0]
    assert decision["exit_kind"] == "full"
    assert receipt.p12_package_digests is not None
    assert receipt.p12_wire_digests is not None


def test_published_fixture_account_and_wire_digests_match_p12_oracle(pinned_checkout, monkeypatch):
    """The actual published-fixture matching gate: the bound P12 tree ships the
    exact decision_set payloads the P12 account consumed, and the P12 oracle
    for the ConfigurationBinding is digest(_package_content(validated)) - the
    same helper shape as product_daily.application.desktop.service._package_content.

    Each fixture is loaded exactly once through the pinned loader; the
    coordinate matcher then recomputes the account digest from THAT validated
    object.  Both expected pairs are the live P12 account values recorded for
    these exact fixtures, so a regression that swaps the wire digest back into
    the binding surface (or vice versa) fails here.
    """
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    import dataclasses as _dc

    from quanthus.product_daily.recommendation.publish import (
        _validate_consumer_object,
    )

    loader = resolve_pinned_v2_loader(ROOT)
    fixture_dir = pinned_checkout / "tests" / "fixtures" / "p12" / "daily-advice-v2"
    expected = {
        "p11-f36fa95e-reduce.json": (
            "sha256:a9b29b59ef10a5f10295110c46548f9d0fcb181cf4c51d8acf0aceaf42bac7cc",
            "sha256:0033fe5b1c6d76628141eef443e9b78fdc9381812e6dc7c31234c4c2698972a2",
        ),
        "full.json": (
            "sha256:925071d06417e55df872ca417cde584141e7f359ebbc94b9d2fd7d2b1071cef6",
            "sha256:91d834f18b20b576ee47cf4f7d0cdb91ddab450f7fbfd969e34b88fd7cf7be44",
        ),
    }
    for name, (expected_wire, expected_account) in expected.items():
        raw = json.loads((fixture_dir / name).read_text())
        # One pinned call per set; the returned (object, wire, account) triple
        # is reused for every surface so no second invocation can diverge.
        validated, wire_digest, account_digest = loader(raw)
        assert wire_digest == expected_wire, name
        assert account_digest == expected_account, name
        matched = _validate_consumer_object(
            raw,
            validated,
            wire_version="p12.decision-set.v2",
            wire_digest=wire_digest,
            account_digest=account_digest,
        )
        # The matcher returns the account binding identity from the SAME
        # validated object - equal to the P12 oracle, and distinct from the
        # wire identity.
        assert matched == expected_account, name
        assert matched != wire_digest, name

        # Cross-check against the pinned tree's own fact_contracts so the
        # expected value is tied to the real oracle, not only to a constant.
        import quanthus as _q

        _q.__path__.insert(0, str(pinned_checkout / "src" / "quanthus"))
        try:
            import quanthus.shared.fact_contracts as fc

            account_content = json.loads(
                json.dumps(_dc.asdict(validated), default=str, sort_keys=True)
            )
            assert fc.digest(account_content) == expected_account, name
            assert fc.digest(raw) == expected_wire, name
        finally:
            _q.__path__.remove(str(pinned_checkout / "src" / "quanthus"))
            _evict_pinned()


def test_publish_fails_before_write_when_loader_refuses(pinned_checkout, tmp_path, monkeypatch):
    """A tampered package refused by the pinned loader leaves zero residue."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    import dataclasses

    from quanthus.product_daily.recommendation.publish import publish_package

    package = _publishable_reduce_package()
    ds = package.decision_sets[0]
    # Remove exit_kind so the pinned v2 loader must refuse before any file lands.
    # object.__setattr__ bypasses the frozen dataclass guard so the mutation
    # reaches the real loader rather than dying at P11's own contract check.
    # Bypass the frozen dataclass: a "full" object is legal P11, then its
    # exit_kind is cleared so the serialized wire no longer matches the
    # validated object the pinned loader would produce.
    tampered_decision = ds.decisions[0]
    object.__setattr__(tampered_decision, "exit_kind", None)
    tampered_ds = dataclasses.replace(ds, decisions=(tampered_decision,))
    tampered_package = dataclasses.replace(package, decision_sets=(tampered_ds,))
    with pytest.raises(ProductDailyError):
        publish_package(
            tampered_package,
            directory=tmp_path,
            published_at=ds.published_at,
        )
    assert not list(tmp_path.glob("*.json"))


def test_publish_refuses_tampered_wire(pinned_checkout, tmp_path, monkeypatch):
    """A v2 wire whose reduce target is not a plain decimal string is refused
    by the pinned loader before any file is staged - zero residue."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    from quanthus.product_daily.recommendation.publish import publish_package

    package = _publishable_reduce_package()
    ds = package.decision_sets[0]
    # Patch the wire serialization so the raw decision_set the pinned loader
    # reads carries a float target - legal to emit, illegal on the v2 wire.
    real_serialize = __import__(
        "quanthus.product_daily.recommendation.publish", fromlist=["_serialize_decision_sets"]
    )._serialize_decision_sets

    def _tampered(pkg, *, wire_version):
        sets = real_serialize(pkg, wire_version=wire_version)
        sets[0]["decisions"][0]["target_position_fraction"] = 0.5  # float
        return sets

    monkeypatch.setattr(
        "quanthus.product_daily.recommendation.publish._serialize_decision_sets",
        _tampered,
    )
    with pytest.raises(Exception, match="decimal string"):
        publish_package(
            package,
            directory=tmp_path,
            published_at=ds.published_at,
        )
    assert not list(tmp_path.glob("*.json"))


def test_v1_release_still_uses_v1_loader_and_bytes(pinned_checkout, tmp_path, monkeypatch):
    """The pinned v1 synthetic consumer path is untouched: old bytes, old digest
    surface, no decision_set_schema marker."""
    monkeypatch.setenv("QUANTHUS_P12_CONSUMER_ROOT", str(pinned_checkout))
    from quanthus.product_daily.recommendation.publish import publish_package
    from tests.unit.test_product_daily_recommendation import _p12_loader

    validate_raw, _ = _p12_loader()

    def v1_loader(raw):
        decision = validate_raw(raw["decisions"][0], raw)
        from datetime import date, datetime

        from quanthus.capabilities.decision_access import MarketDecisionSet
        from quanthus.shared.fact_contracts import digest as p12_digest
        from quanthus.shared.values import VersionRef

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
    payload = _written_payload(tmp_path, result.package)
    assert payload["schema_version"] == "quanthus.r11.daily-advice.v1"
    assert "decision_set_schema" not in payload["decision_sets"][0]
    assert "exit_kind" not in payload["decision_sets"][0]["decisions"][0]
    assert receipt.p12_package_digests is not None
