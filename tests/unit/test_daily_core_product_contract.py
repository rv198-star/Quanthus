from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.daily_core_domain import (
    DailyDomainBuildError,
    advance_daily_current,
    verify_daily_domain_increment,
)
from quanthus.pipelines.securities_warehouse.daily_core_ods_catalog import (
    DAILY_CORE_PRODUCT_SCOPE,
    DailyOdsCatalogError,
    _canonical_bytes,
    _daily_core_quality_sha256,
    build_daily_ods_catalog,
    verify_daily_ods_catalog,
)

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "ops/bin/run-cn01-ods-daily"


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_release(root: Path, date_str: str, prefix: str, *, contract: str = "r1") -> Path:
    subdir = root / "daily" / date_str / ("event" if "calendar" in prefix else "market")
    subdir.mkdir(parents=True)
    (subdir / "artifacts").mkdir()
    payload = subdir / "artifacts/payload.parquet"
    payload.write_bytes(prefix.encode())
    manifest = {
        "schema_version": 1,
        "release_id": prefix + date_str.replace("-", "") + "-v2",
        "schedule": "daily",
        "market_date": date_str,
        "created_at": date_str + "T10:00:00+00:00",
        "producer": "synthetic",
        "status": "ready",
        "schedule_contract_id": "contract-v1",
        "schedule_contract_revision": contract,
        "schedule_config_sha256": "sha256:" + "1" * 64,
        "artifacts": [
            {
                "name": "payload",
                "relative_path": "artifacts/payload.parquet",
                "sha256": sha(payload.read_bytes()),
                "size_bytes": payload.stat().st_size,
                "source_revision": "synthetic-v1",
                "knowledge_cutoff": date_str + "T10:00:00+00:00",
            }
        ],
    }
    raw = json.dumps(manifest, sort_keys=True).encode()
    (subdir / "release.json").write_bytes(raw)
    (subdir / "READY").write_text(sha(raw) + "\n")
    return subdir


def test_core_two_verified_lanes_do_not_require_fake_1m_or_5m(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    write_release(releases, "2026-09-24", "hermes-market-daily-core-")
    write_release(releases, "2026-09-24", "hermes-calendar-daily-events-")
    target = tmp_path / "core-catalog"
    result = build_daily_ods_catalog(
        release_root=releases,
        market_date=date(2026, 9, 24),
        output=target,
        product_scope=DAILY_CORE_PRODUCT_SCOPE,
    )
    assert result.product_scope == DAILY_CORE_PRODUCT_SCOPE
    assert result.lane_count == 2
    o = json.loads((target / "catalog.json").read_text())
    assert o["schema_version"] == 2
    assert {x["lane"] for x in o["lanes"]} == {"market_daily_core", "calendar_daily_events"}
    assert {x["lane"] for x in o["excluded_optional_lanes"]} == {"intraday_1m", "intraday_5m"}
    assert all(x["formal_ready"] is False for x in o["excluded_optional_lanes"])
    assert o["quality_policy_sha256"] == _daily_core_quality_sha256()
    assert verify_daily_ods_catalog(target) == result
    with pytest.raises(DailyOdsCatalogError, match="required ODS lane"):
        build_daily_ods_catalog(
            release_root=releases, market_date=date(2026, 9, 24), output=tmp_path / "legacy"
        )
    with pytest.raises(DailyOdsCatalogError, match="product identity"):
        build_daily_ods_catalog(release_root=releases, market_date=date(2026, 9, 24), output=target)


def test_cached_catalog_checks_exact_bound_sources_under_prevention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    day = date(2026, 10, 13)
    root = tmp_path / "releases"
    monkeypatch.delenv("HERMES_ODS_PREVENTION_POLICY", raising=False)
    sources = [
        write_release(root, str(day), prefix)
        for prefix in ["hermes-market-daily-core-", "hermes-calendar-daily-events-"]
    ]
    old = tmp_path / "old-catalog"
    kwargs = {"release_root": root, "market_date": day, "product_scope": DAILY_CORE_PRODUCT_SCOPE}
    build_daily_ods_catalog(**kwargs, output=old)
    original_catalog = (old / "catalog.json").read_bytes()
    for source in sources:
        fresh = source.with_name(source.name + "-fresh")
        shutil.copytree(source, fresh)
        manifest = json.loads((fresh / "release.json").read_text())
        manifest["release_id"] += "-fresh"
        manifest["created_at"] = "2026-10-13T11:00:00+00:00"
        for name in ["source_universe", "acquisition_quality", "original_responses"]:
            artifact = fresh / "artifacts" / (name + ".json")
            artifact.write_text(
                json.dumps({"policy_id": "p10-acquisition-prevention-v1", "market_date": str(day)})
            )
            manifest["artifacts"].append(
                {
                    "name": name,
                    "relative_path": artifact.relative_to(fresh).as_posix(),
                    "sha256": sha(artifact.read_bytes()),
                    "size_bytes": artifact.stat().st_size,
                    "source_revision": "synthetic-v1",
                    "knowledge_cutoff": manifest["created_at"],
                }
            )
        raw = json.dumps(manifest, sort_keys=True).encode()
        (fresh / "release.json").write_bytes(raw)
        (fresh / "READY").write_text(sha(raw) + "\n")
    monkeypatch.setenv("HERMES_ODS_PREVENTION_POLICY", "p10-acquisition-prevention-v1")
    with pytest.raises(DailyOdsCatalogError, match="cached catalog source fails"):
        build_daily_ods_catalog(**kwargs, output=old)
    assert (old / "catalog.json").read_bytes() == original_catalog
    new = tmp_path / "new-catalog"
    accepted = build_daily_ods_catalog(**kwargs, output=new)
    assert build_daily_ods_catalog(**kwargs, output=new) == accepted
    monkeypatch.delenv("HERMES_ODS_PREVENTION_POLICY")
    assert build_daily_ods_catalog(**kwargs, output=old).market_date == day


@pytest.mark.parametrize("missing", ["market", "event"])
def test_required_daily_lanes_still_block(tmp_path: Path, missing: str) -> None:
    releases = tmp_path / "releases"
    if missing != "market":
        write_release(releases, "2026-09-24", "hermes-market-daily-core-")
    if missing != "event":
        write_release(releases, "2026-09-24", "hermes-calendar-daily-events-")
    with pytest.raises(DailyOdsCatalogError, match="required ODS lane"):
        build_daily_ods_catalog(
            release_root=releases,
            market_date=date(2026, 9, 24),
            output=tmp_path / "cat",
            product_scope=DAILY_CORE_PRODUCT_SCOPE,
        )


def test_incompatible_contract_or_wrong_date_blocks_before_daily_publish(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    write_release(releases, "2026-09-24", "hermes-market-daily-core-", contract="r1")
    write_release(releases, "2026-09-24", "hermes-calendar-daily-events-", contract="r2")
    with pytest.raises(DailyOdsCatalogError, match="schedule contract"):
        build_daily_ods_catalog(
            release_root=releases,
            market_date=date(2026, 9, 24),
            output=tmp_path / "cat",
            product_scope=DAILY_CORE_PRODUCT_SCOPE,
        )
    with pytest.raises(DailyOdsCatalogError, match="required ODS lane"):
        build_daily_ods_catalog(
            release_root=releases,
            market_date=date(2026, 9, 23),
            output=tmp_path / "other",
            product_scope=DAILY_CORE_PRODUCT_SCOPE,
        )


def test_fake_quality_scope_rejected_even_with_rehashed_catalog(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    write_release(root, "2026-09-24", "hermes-market-daily-core-")
    write_release(root, "2026-09-24", "hermes-calendar-daily-events-")
    dest = tmp_path / "cat"
    build_daily_ods_catalog(
        release_root=root,
        market_date=date(2026, 9, 24),
        output=dest,
        product_scope=DAILY_CORE_PRODUCT_SCOPE,
    )
    o = json.loads((dest / "catalog.json").read_text())
    o["excluded_optional_lanes"][0]["formal_ready"] = True
    o.pop("catalog_digest")
    o["catalog_digest"] = sha(_canonical_bytes(o))
    payload = _canonical_bytes(o) + b"\n"
    (dest / "catalog.json").write_bytes(payload)
    (dest / "READY").write_text(sha(payload) + "\n")
    with pytest.raises(DailyOdsCatalogError, match="product contract"):
        verify_daily_ods_catalog(dest)


def _empty_core_package(tmp_path: Path) -> Path:
    folder = tmp_path / "pkg"
    folder.mkdir()
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines.securities_warehouse.daily_core_domain import _TABLE_KEYS

    counts = {}
    for name in _TABLE_KEYS:
        path = (
            folder / "payload" / "fixture" / name / "market_date=2026-09-24" / "part-00000.parquet"
        )
        path.parent.mkdir(parents=True)
        rows = (
            [{"interval_size": "1d", "price_basis": "none"}]
            if name == "dwd_security_bar"
            else [{"fixture_id": "one"}]
        )
        pq.write_table(pa.Table.from_pylist(rows), path)
        counts[name] = 1
    (folder / "audit.json").write_text(
        json.dumps({"result": "accepted", "market_date": "2026-09-24"})
    )
    filelist = [
        {
            "path": file.relative_to(folder).as_posix(),
            "sha256": sha(file.read_bytes()),
            "size_bytes": file.stat().st_size,
        }
        for file in sorted(folder.rglob("*"))
        if file.is_file()
    ]
    manifest = {
        "schema_version": 2,
        "kind": "cn01_daily_core_dwd_increment",
        "package_id": "daily-core-20260924-test",
        "market_date": "2026-09-24",
        "product_scope": DAILY_CORE_PRODUCT_SCOPE,
        "quality_policy": "p10-business-value-warehouse-quality.v1",
        "quality_policy_sha256": _daily_core_quality_sha256(),
        "consumable_intervals": ["1d"],
        "selected_releases": {"market_daily_core": {}, "calendar_daily_events": {}},
        "unavailable_products": {
            "intraday_1m": "not_part_of_daily_core_not_qualified_as_daily",
            "intraday_5m": "not_part_of_daily_core_see_separate_product_status",
        },
        "files": filelist,
        "row_counts": counts,
        "content_digest": sha(_canonical_bytes(filelist)),
    }
    content = _canonical_bytes(manifest) + b"\n"
    (folder / "manifest.json").write_bytes(content)
    (folder / "READY").write_text(sha(content) + "\n")
    return folder


def test_new_product_pointer_cannot_overwrite_legacy_current(tmp_path: Path) -> None:
    source = _empty_core_package(tmp_path)
    assert verify_daily_domain_increment(source).package_id == "daily-core-20260924-test"
    with pytest.raises(DailyDomainBuildError, match="must not overwrite legacy"):
        advance_daily_current(package=source, pointer=tmp_path / "CURRENT.json")
    assert not (tmp_path / "CURRENT.json").exists()
    core = tmp_path / "CURRENT_DAILY_CORE.json"
    advance_daily_current(package=source, pointer=core)
    pointer = json.loads(core.read_text())
    assert pointer["kind"] == "cn01_daily_core_dwd_current"
    assert pointer["schema_version"] == 2
    assert pointer["product_scope"] == DAILY_CORE_PRODUCT_SCOPE


def test_new_dwd_manifest_rejects_1m_selected_release(tmp_path: Path) -> None:
    source = _empty_core_package(tmp_path)
    o = json.loads((source / "manifest.json").read_text())
    o["selected_releases"]["intraday_1m"] = {}
    content = _canonical_bytes(o) + b"\n"
    (source / "manifest.json").write_bytes(content)
    (source / "READY").write_text(sha(content) + "\n")
    with pytest.raises(DailyDomainBuildError, match="product contract"):
        verify_daily_domain_increment(source)


def _script(path: Path, source: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf8")
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("after", "target", "sessions", "error"),
    [
        (
            "2026-09-24",
            "2026-09-28",
            [("2026-09-25", 0), ("2026-09-26", 0), ("2026-09-27", 0), ("2026-09-28", 1)],
            None,
        ),
        (
            "2026-09-24",
            "2026-09-29",
            [
                ("2026-09-25", 0),
                ("2026-09-26", 0),
                ("2026-09-27", 0),
                ("2026-09-28", 1),
                ("2026-09-29", 1),
            ],
            "skip required open session",
        ),
        (
            "2026-09-24",
            "2026-09-28",
            [("2026-09-26", 0), ("2026-09-27", 0), ("2026-09-28", 1)],
            "calendar date coverage missing",
        ),
        (
            "2026-09-24",
            "2026-09-28",
            [("2026-09-25", 0), ("2026-09-26", 0), ("2026-09-27", 0), ("2026-09-28", 0)],
            "not an open session",
        ),
    ],
)
def test_daily_core_never_skips_open_session_or_unverified_calendar(
    after: str, target: str, sessions: list, error: str | None
) -> None:
    from quanthus.pipelines.securities_warehouse.daily_core_domain import (
        _verify_daily_core_calendar_continuity,
    )

    class _Table:
        def __init__(self, rows) -> None:
            self.rows = rows

        def to_pylist(self):
            return self.rows

    class _Reader:
        @staticmethod
        def read_table(_path, *, columns):
            assert set(columns) == {"exchange", "trade_date", "is_open", "session"}
            return _Table(
                [
                    {
                        "exchange": venue,
                        "trade_date": date.fromisoformat(day),
                        "session": "day",
                        "is_open": flag,
                    }
                    for day, flag in sessions
                    for venue in ("SSE", "SZSE")
                ]
            )

    def invoke() -> None:
        _verify_daily_core_calendar_continuity(
            Path("/fixture/calendar.parquet"),
            after=date.fromisoformat(after),
            target=date.fromisoformat(target),
            parquet=_Reader,
        )

    if error is not None:
        with pytest.raises(DailyDomainBuildError, match=error):
            invoke()
    else:
        invoke()


def test_core_empty_package_cannot_be_marked_ready(tmp_path: Path) -> None:
    folder = _empty_core_package(tmp_path)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["row_counts"] = {}
    raw = _canonical_bytes(manifest) + b"\n"
    (folder / "manifest.json").write_bytes(raw)
    (folder / "READY").write_text(sha(raw) + "\n")
    with pytest.raises(DailyDomainBuildError, match="required table inventory"):
        verify_daily_domain_increment(folder)


def test_core_same_date_immutable(tmp_path: Path) -> None:
    folder = _empty_core_package(tmp_path)
    pointer = tmp_path / "CURRENT_DAILY_CORE.json"
    advance_daily_current(package=folder, pointer=pointer)
    before = pointer.read_bytes()
    advance_daily_current(package=folder, pointer=pointer)
    assert pointer.read_bytes() == before
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["package_id"] = "different-same-date"
    raw = _canonical_bytes(manifest) + b"\n"
    (folder / "manifest.json").write_bytes(raw)
    (folder / "READY").write_text(sha(raw) + "\n")
    with pytest.raises(DailyDomainBuildError, match="same-date publication"):
        advance_daily_current(package=folder, pointer=pointer)
    assert pointer.read_bytes() == before


def test_real_minute_payload_cannot_use_daily_core_label(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    folder = _empty_core_package(tmp_path)
    bar = next(folder.glob("payload/*/dwd_security_bar/*/*.parquet"))
    pq.write_table(pa.Table.from_pylist([{"interval_size": "1m", "price_basis": "none"}]), bar)
    manifest = json.loads((folder / "manifest.json").read_text())
    for entry in manifest["files"]:
        path = folder / entry["path"]
        entry["sha256"] = sha(path.read_bytes())
        entry["size_bytes"] = path.stat().st_size
    manifest["content_digest"] = sha(_canonical_bytes(manifest["files"]))
    raw = _canonical_bytes(manifest) + b"\n"
    (folder / "manifest.json").write_bytes(raw)
    (folder / "READY").write_text(sha(raw) + "\n")
    with pytest.raises(DailyDomainBuildError, match="non-daily"):
        verify_daily_domain_increment(folder)


def test_new_product_retains_dated_pointer(tmp_path: Path) -> None:
    folder = _empty_core_package(tmp_path)
    current = tmp_path / "CURRENT_DAILY_CORE.json"
    advance_daily_current(package=folder, pointer=current)
    dated = tmp_path / "date-pointers/2026-09-24.json"
    assert dated.read_bytes() == current.read_bytes()


@pytest.mark.parametrize("mutation", [None, "purpose", "execution", "base", "code", "config"])
def test_base24_daily_offline_governance_identity(tmp_path: Path, mutation: str | None) -> None:
    from quanthus.pipelines.securities_warehouse.base24_image import KIND

    root = _empty_core_package(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest.update(
        base={"kind": KIND, "manifest_sha256": "sha256:" + "a" * 64},
        code_revision="b" * 40,
        catalog_sha256="sha256:" + "c" * 64,
        prior=None,
        purpose="isolated_daily_governance",
        execution_authorized=False,
        consumer_admission="NOT_GRANTED",
    )
    manifest["job_identity"] = {
        "base": dict(manifest["base"]),
        "catalog": manifest["catalog_sha256"],
        "code": manifest["code_revision"],
        "config": "sha256:" + "d" * 64,
        "prior": None,
    }
    if mutation == "purpose":
        manifest["purpose"] = "production"
    elif mutation == "execution":
        manifest["execution_authorized"] = True
    elif mutation == "base":
        manifest["job_identity"]["base"] = {"kind": "legacy"}
    elif mutation == "code":
        manifest["job_identity"]["code"] = "e" * 40
    elif mutation == "config":
        manifest["job_identity"]["config"] = "sha256:" + "x" * 64
    raw = _canonical_bytes(manifest) + b"\n"
    (root / "manifest.json").write_bytes(raw)
    (root / "READY").write_text(sha(raw) + "\n")
    if mutation:
        with pytest.raises(DailyDomainBuildError, match="governance/job identity"):
            verify_daily_domain_increment(root)
    else:
        assert verify_daily_domain_increment(root).manifest_sha256 == sha(raw)
