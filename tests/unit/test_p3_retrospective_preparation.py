"""A real shared preparation on disposable R2 observations, without a replay."""

import gzip
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace

import pyarrow.parquet as pq
import pytest

from quanthus.expert_strategy import p3_base24_native as native
from quanthus.expert_strategy import p3_retrospective_preparation as prep
from tests.unit.test_p3_retrospective_bridge import INFO, ROOT, SOURCE
from tests.unit.test_p3_retrospective_features import DAYS, ports

SCOPE = (DAYS[61], DAYS[63], DAYS[64])


def build(tmp_path, *, observations=None, **kwargs):
    return prep.materialize(
        ROOT,
        tmp_path / "prepared",
        ports=ports() if observations is None else observations,
        trading_dates=DAYS,
        source_manifest_sha256=SOURCE,
        information_cutoff=INFO,
        support_manifest_sha256=SOURCE,
        input_manifest_sha256=SOURCE,
        industry_manifest_sha256=SOURCE,
        source_roots=(tmp_path / "source",),
        scope=SCOPE,
        synthetic=True,
        **kwargs,
    )


def test_one_stream_one_feature_panel_and_identical_four_variant_inputs(tmp_path):
    class Once:
        calls = 0

        def __iter__(self):
            self.calls += 1
            assert self.calls == 1
            return iter(ports())

    observations = Once()
    result = build(tmp_path, observations=observations)
    root = tmp_path / "prepared"
    assert result["status"] == "RESULT_BLIND_SHARED_PREPARATION_COMPLETE"
    assert result["candidates"] == result["eligible_candidates"] == 1
    assert result["feature_rows"] == 3
    assert result["source_stream_builds"] == result["feature_panel_builds"] == 1
    assert result["real_comparisons_executed"] == 0
    assert result["D_activated"] is result["original_B_accepted"] is False
    variants = json.loads((root / "variants.json").read_text())
    assert tuple(v["variant_id"] for v in variants) == ("B", "T", "R", "TR")
    assert len({v["shared_prerequisite_digest"] for v in variants}) == 1
    assert all(v["execution_authorized"] is False for v in variants)
    assert not (root / "history-TMP.parquet").exists()
    assert not (root / "READY").exists()  # this is never a B/D admission package
    assert (
        prep.verify(ROOT, root, manifest_sha256=result["manifest_sha256"])["eligible_candidates"]
        == 1
    )
    with gzip.open(root / "ports.jsonl.gz", "rt") as stream:
        first = prep.decode_port(json.loads(next(stream)))
    assert first.information_cutoff == INFO
    assert (
        first.selected.bar.close_price.as_tuple()
        == next(ports()).selected.bar.close_price.as_tuple()
    )
    assert first.selected.candidate.knowledge_cutoff > first.selected.close_at


@pytest.mark.parametrize("field", ["missing", "unknown_action", "missing_clock"])
def test_unknowns_block_the_common_input_instead_of_becoming_qualified(tmp_path, field):
    result = build(tmp_path, observations=ports(**{field: 61}))
    assert result["eligible_candidates"] == 0
    assert result["real_comparisons_executed"] == 0
    assert result["support_qualified_by_this_package"] is False
    rows = pq.read_table(tmp_path / "prepared/features.parquet").to_pylist()
    assert not rows[0]["entry_eligible"]


def test_two_relocations_without_sources_and_corruption_fails_closed(tmp_path):
    result = build(tmp_path)
    old = tmp_path / "prepared"
    for n in (1, 2):
        new = tmp_path / f"moved-{n}"
        shutil.copytree(old, new)
        shutil.rmtree(old)
        assert prep.verify(ROOT, new, manifest_sha256=result["manifest_sha256"]) == result
        old = new
    with (old / "features.parquet").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(native.InputError, match="MEMBER"):
        prep.verify(ROOT, old, manifest_sha256=result["manifest_sha256"])


def test_output_isolation_and_no_overwrite_precede_iteration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    original = source / "keep"
    original.write_bytes(b"immutable")

    def never():
        pytest.fail("input stream read before preflight")
        yield

    arguments = dict(
        ports=never(),
        trading_dates=DAYS,
        source_manifest_sha256=SOURCE,
        information_cutoff=INFO,
        support_manifest_sha256=SOURCE,
        input_manifest_sha256=SOURCE,
        industry_manifest_sha256=SOURCE,
        source_roots=(source,),
        scope=SCOPE,
        synthetic=True,
    )
    for output in (source, source / "output"):
        with pytest.raises(native.InputError):
            prep.materialize(ROOT, output, **arguments)
    assert list(source.iterdir()) == [original] and original.read_bytes() == b"immutable"
    build(tmp_path)
    with pytest.raises(native.InputError, match="OUTPUT_EXISTS"):
        prep.materialize(ROOT, tmp_path / "prepared", **arguments)


def test_changed_snapshot_duplicate_or_unfrozen_scope_fails_without_completion(tmp_path):
    p = next(ports())
    for n, values in enumerate(
        ((p, p), (replace(p, source_manifest_sha256="sha256:" + "b" * 64),))
    ):
        parent = tmp_path / str(n)
        parent.mkdir()
        with pytest.raises(native.InputError):
            build(parent, observations=values)
        assert not (parent / "prepared/PREPARATION_COMPLETE.json").exists()
    with pytest.raises(native.InputError, match="SCOPE"):
        prep.materialize(
            ROOT,
            tmp_path / "illegal",
            ports=(),
            trading_dates=DAYS,
            source_manifest_sha256=SOURCE,
            information_cutoff=INFO,
            support_manifest_sha256=SOURCE,
            source_roots=(tmp_path / "source",),
            input_manifest_sha256=SOURCE,
            industry_manifest_sha256=SOURCE,
            scope=SCOPE,
        )
    assert not (tmp_path / "illegal").exists()


def test_two_cold_processes_use_only_moved_prerequisite_and_deny_native_sources_and_network(
    tmp_path,
):
    import ctypes.util

    if not ctypes.util.find_library("seccomp"):
        pytest.skip("libseccomp unavailable; native network denial not checked")
    result = build(tmp_path)
    forbidden = tmp_path / "old-source"
    forbidden.mkdir()
    original = forbidden / "unreachable"
    original.write_bytes(b"must not open")
    script = r"""
import ctypes, ctypes.util, errno, json, os, socket, sys
from pathlib import Path
root, repository, scratch, forbidden = map(Path, sys.argv[1:])
libc = ctypes.CDLL(None, use_errno=True)
class Ruleset(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64)]
class PathRule(ctypes.Structure):
    _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]
abi = libc.syscall(444, 0, 0, 1)
assert abi >= 1, 'Landlock unavailable; source isolation was not verified'
access = 1 | 4 | 8
spec = Ruleset(access)
fd = libc.syscall(444, ctypes.byref(spec), ctypes.sizeof(spec), 0)
assert fd >= 0
allowed = [root, repository/'src', repository/'config', scratch, Path(sys.prefix),
           Path('/usr'), Path('/lib'), Path('/lib64'), Path('/etc'), Path('/dev'),
           Path('/proc'), Path('/sys')]
for path in allowed:
    if not path.exists(): continue
    parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
    rule = PathRule(access, parent)
    assert libc.syscall(445, fd, 1, ctypes.byref(rule), 0) == 0
    os.close(parent)
assert libc.prctl(38, 1, 0, 0, 0) == 0
assert libc.syscall(446, fd, 0) == 0
os.close(fd)
sec = ctypes.CDLL(ctypes.util.find_library('seccomp'))
sec.seccomp_init.argtypes=[ctypes.c_uint32]; sec.seccomp_init.restype=ctypes.c_void_p
sec.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
sec.seccomp_syscall_resolve_name.restype=ctypes.c_int
sec.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
sec.seccomp_load.argtypes=[ctypes.c_void_p]; sec.seccomp_release.argtypes=[ctypes.c_void_p]
ctx=sec.seccomp_init(0x7fff0000)
assert ctx
for name in (b'socket', b'socketpair', b'connect'):
    syscall = sec.seccomp_syscall_resolve_name(name)
    assert sec.seccomp_rule_add(ctx,0x50000|errno.EACCES,syscall,0)==0
assert sec.seccomp_load(ctx)==0
sec.seccomp_release(ctx)
try: socket.socket(); raise AssertionError('native network allowed')
except PermissionError: pass
try: forbidden.read_bytes(); raise AssertionError('old source allowed')
except PermissionError: pass
import pyarrow as pa
try: pa.OSFile(str(forbidden), 'r'); raise AssertionError('native old source allowed')
except PermissionError: pass
from quanthus.expert_strategy.p3_retrospective_preparation import verify
print(json.dumps(verify(repository,root,manifest_sha256=os.environ['PREPARATION_SHA']),sort_keys=True))
"""
    previous = tmp_path / "prepared"
    for n in (1, 2):
        moved, scratch = tmp_path / f"cold-{n}", tmp_path / f"cold-scratch-{n}"
        shutil.copytree(previous, moved)
        shutil.rmtree(previous)
        scratch.mkdir()
        completed = subprocess.run(
            [sys.executable, "-c", script, str(moved), str(ROOT), str(scratch), str(original)],
            cwd=scratch,
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(ROOT / "src"),
                "TMPDIR": str(scratch),
                "XDG_CACHE_HOME": str(scratch),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "PREPARATION_SHA": result["manifest_sha256"],
            },
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == result
        previous = moved


def test_independent_shared_builds_preserve_typed_semantic_bytes(tmp_path):
    for n in (1, 2):
        parent = tmp_path / str(n)
        parent.mkdir()
        build(parent)
    for name in prep.MEMBERS:
        assert (tmp_path / "1/prepared" / name).read_bytes() == (
            tmp_path / "2/prepared" / name
        ).read_bytes()
