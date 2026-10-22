"""Real snapshot semantics on synthetic files; strict P3 admission stays closed."""

import json
import shutil
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_input as retro
from tests.unit.test_p3_base24_native import mutate, sha
from tests.unit.test_p3_retrospective_purpose import ROOT, run
from tests.unit.test_p3_retrospective_purpose import source as source_fixture


@pytest.fixture
def source(tmp_path):
    return source_fixture.__wrapped__(tmp_path)


def build(source, output, tmp_path):
    assessment = run(source, tmp_path)
    warmup = json.loads((source.parent / "synthetic-warmup-authority.json").read_text())
    return retro.prepare(source, output, assessment=assessment, warmup=warmup, repository=ROOT)


def reseal(root):
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"] = {name: native._entry(root / name) for name in manifest["files"]}
    (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    (root / "READY").write_text(sha(root / "manifest.json") + "\n")
    return sha(root / "manifest.json")


def test_input_moves_twice_without_original_sources_or_time_rewrite(source, tmp_path):
    original = pq.read_table(source / "native/dwd_security_bar.parquet")
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    assert result["source_fingerprints_unchanged"]
    assert result["original_B_accepted"] is result["D_activated"] is False
    copied = pq.read_table(out / "native/dwd_security_bar.parquet")
    assert original.to_pylist() == copied.to_pylist()
    assert original.schema.field("close").type == copied.schema.field("close").type
    shutil.rmtree(source)  # Disposable synthetic source only.
    for number in (1, 2):
        moved = tmp_path / f"moved-{number}"
        shutil.copytree(out, moved)
        shutil.rmtree(out)
        cold = retro.verify(moved, manifest_sha256=result["manifest_sha256"])
        assert cold["source_paths_opened"] is False
        assert cold["tables"] == result["tables"]
        out = moved
    with pytest.raises(native.InputError):
        native.verify(out, manifest_sha256=result["manifest_sha256"])


def test_unknown_records_remain_separate_and_unavailable(source, tmp_path):
    mutate(source, "dwd_security_bar", lambda rows: rows[0].update(knowledge_from=None))
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    assert result["tables"]["dwd_security_bar"] == {
        "snapshot_rows": 0,
        "unknown_rows_retained_unavailable": 1,
    }
    assert (
        pq.read_table(out / "unavailable/dwd_security_bar.parquet")["knowledge_from"].null_count
        == 1
    )


def test_sealed_input_cannot_be_overwritten(source, tmp_path):
    out = tmp_path / "out"
    build(source, out, tmp_path)
    before = sha(out / "manifest.json")
    with pytest.raises(native.InputError, match="RETRO_OUTPUT_OR_STAGING_EXISTS"):
        build(source, out, tmp_path)
    assert sha(out / "manifest.json") == before


def test_staging_source_overlap_rejected_before_writing(source, tmp_path):
    out = tmp_path / "out"
    assessment = run(source, tmp_path)
    warmup = json.loads((source.parent / "synthetic-warmup-authority.json").read_text())
    moved = tmp_path / ".out.job"
    source.rename(moved)
    before = {p.relative_to(moved).as_posix(): sha(p) for p in moved.rglob("*") if p.is_file()}
    with pytest.raises(native.InputError):
        retro.prepare(moved, out, assessment=assessment, warmup=warmup, repository=ROOT)
    assert not out.exists()
    assert before == {
        p.relative_to(moved).as_posix(): sha(p) for p in moved.rglob("*") if p.is_file()
    }


def test_corrupt_input_fails_without_source_fallback(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    path = out / "native/dwd_security_bar.parquet"
    path.write_bytes(path.read_bytes() + b"damaged")
    with pytest.raises(native.InputError, match="MEMBER_CHANGED_OR_INVALID"):
        retro.verify(out, manifest_sha256=result["manifest_sha256"])


def test_expired_whole_job_budget_cannot_start_another_verifier_window(source, tmp_path):
    out = tmp_path / "out"
    result = build(source, out, tmp_path)
    with pytest.raises(native.InputError, match="WALL_BUDGET_EXCEEDED"):
        retro.verify(out, manifest_sha256=result["manifest_sha256"], deadline=time.monotonic() - 1)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"calendar_session_id": "sz-2025-01-03"}, "RETRO_DATE_VENUE_OR_WARMUP"),
        ({"source_artifact_sha256": None}, "RETRO_KEY_OR_LINEAGE"),
    ],
)
def test_repackaged_semantic_damage_is_rejected(source, tmp_path, change, reason):
    out = tmp_path / "out"
    build(source, out, tmp_path)
    path = out / "native/dwd_tradability_state.parquet"
    data = pq.read_table(path)
    rows = data.to_pylist()
    rows[0].update(change)
    pq.write_table(pa.Table.from_pylist(rows, schema=data.schema), path)
    digest = reseal(out)
    with pytest.raises(native.InputError, match=reason):
        retro.verify(out, manifest_sha256=digest)
