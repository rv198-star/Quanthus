"""Reuse only earlier actual prior semantics plus unchanged physical closure."""

import pytest

from quanthus.pipelines import p10_complete_daily as full
from quanthus.pipelines.p10_delivery_common import atomic, file_digest, object_at, seal


def fixture(root):
    package = root / "packages/2026-10-12/prior"
    package.mkdir(parents=True)
    (package / "evidence.txt").write_text("synthetic frozen bytes")
    manifest = {
        "schema_version": 1,
        "kind": full.KIND,
        "package_id": "prior",
        "market_date": "2026-10-12",
        "base": {"manifest_sha256": "base"},
        "member_manifest_sha256": {"1d": "daily", "1m": "m1", "5m": "m5"},
        "code_revision": "a" * 40,
        "minute_row_counts": {"1m": 1, "5m": 1},
        "consumable_intervals": ["1d", "1m", "5m"],
        "minute_quality_grades": {"1m": {"R2": 1}, "5m": {"R0": 1}},
        "prior": None,
        "domain_manifest_sha256": {},
        "policy_id": full.POLICY_ID,
        "policy_sha256": file_digest(full.policy_path()),
    }
    seal(package, manifest)
    manifest = object_at(package / "manifest.json")
    sha = file_digest(package / "manifest.json")
    pointer = {
        "package_id": "prior",
        "market_date": "2026-10-12",
        "manifest_path": "packages/2026-10-12/prior/manifest.json",
        "manifest_sha256": sha,
        "content_digest": manifest["content_digest"],
        "base": manifest["base"],
    }
    atomic(root / "date-pointers/2026-10-12.json", pointer)
    receipt = {
        k: manifest[k]
        for k in [
            "package_id",
            "market_date",
            "content_digest",
            "base",
            "member_manifest_sha256",
            "code_revision",
            "minute_row_counts",
            "consumable_intervals",
            "minute_quality_grades",
            "prior",
            "domain_manifest_sha256",
        ]
    }
    receipt.update(
        manifest_sha256=sha,
        mode="actual_complete_package_files_and_native_projection",
        verified_at="2026-10-13T01:00:00Z",
    )
    atomic(root / "verification" / (sha[7:] + ".json"), receipt)
    return package, pointer, receipt


def test_exact_admitted_prior_reuses_semantics_after_physical_hashing(tmp_path, monkeypatch):
    _, pointer, receipt = fixture(tmp_path)
    monkeypatch.setattr(full, "verify", lambda _: pytest.fail("prior semantics repeated"))
    assert full.verified_prior(tmp_path, pointer) == receipt


@pytest.mark.parametrize("change", ["bytes", "dated", "mode", "rows", "members", "domains", "code"])
def test_existing_changed_prior_proof_never_falls_back(tmp_path, monkeypatch, change):
    package, pointer, receipt = fixture(tmp_path)
    monkeypatch.setattr(full, "verify", lambda _: pytest.fail("invalid receipt fell back"))
    if change == "bytes":
        (package / "evidence.txt").write_text("changed")
    elif change == "dated":
        atomic(tmp_path / "date-pointers/2026-10-12.json", {**pointer, "content_digest": "wrong"})
    else:
        key = {
            "mode": "mode",
            "rows": "minute_row_counts",
            "members": "member_manifest_sha256",
            "domains": "domain_manifest_sha256",
            "code": "code_revision",
        }[change]
        receipt[key] = "wrong"
        atomic(tmp_path / "verification" / (pointer["manifest_sha256"][7:] + ".json"), receipt)
    with pytest.raises(ValueError):
        full.verified_prior(tmp_path, pointer)


def test_only_missing_prior_receipt_uses_original_cold_verification(tmp_path, monkeypatch):
    package, pointer, _ = fixture(tmp_path)
    (tmp_path / "verification" / (pointer["manifest_sha256"][7:] + ".json")).unlink()
    monkeypatch.setattr(full, "verify", lambda path: {"cold": path == package})
    assert full.verified_prior(tmp_path, pointer) == {"cold": True}


@pytest.mark.parametrize("field", ["prior", "domain_manifest_sha256"])
def test_incomplete_existing_prior_receipt_fails_closed(tmp_path, monkeypatch, field):
    _, pointer, receipt = fixture(tmp_path)
    receipt.pop(field)
    atomic(tmp_path / "verification" / (pointer["manifest_sha256"][7:] + ".json"), receipt)
    monkeypatch.setattr(full, "verify", lambda _: pytest.fail("incomplete receipt fell back"))
    with pytest.raises(ValueError, match="physical closure"):
        full.verified_prior(tmp_path, pointer)
