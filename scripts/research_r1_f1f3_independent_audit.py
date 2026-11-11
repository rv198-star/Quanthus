#!/usr/bin/env python3
"""#612 difference audit: isolated fault probes and fixed A4 readback, no market rerun."""

import argparse
import copy
import hashlib
import json
import marshal
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quanthus import hf_dwd_research as worker  # noqa: E402

CANDIDATE = "bb99f99d41dacf73d13fc8dfabe011844d4be374"
EXECUTION = "1351be2b8c8ca181c2711ab81b0a01ce7addd664"
REVISION = "bad249962cdcd801d9ef617ae92577bc5ddefb33"
PREFIX = "runs/RESEARCH-R1-HF-002/RESEARCH-R1-HF-002-T1/A4"
PACKAGE_DIGEST = "sha256:7857bac326143bb999c72941b8356430657b6d95af1d8261db802f3c5b8695a0"
REQUEST = ROOT / "config/research/research-r1/request.v2.a4.json"


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def canonical(value):
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def sealed(value):
    value = {k: v for k, v in value.items() if k != "digest"}
    return {**value, "digest": canonical(value)}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def inventory(path):
    return {p.name: sha(p) for p in path.iterdir() if p.is_file()}


def git_bytes(ref, name):
    return subprocess.check_output(["git", "show", ref + ":" + name], cwd=ROOT)


def provenance(original, retained, out):
    q = read(REQUEST)
    members = read(ROOT / q["closure"]["path"])["members"]
    assert sha(ROOT / q["closure"]["path"]) == q["closure"]["sha256"]
    assert len(members) == 56 and sum(x["size_bytes"] for x in members) == 187921175
    seed = out / "seed"
    seed.mkdir(parents=True)
    names = ("fetch.json", "result.json", "run.json", "resources.json", "attempts.json")
    for name in names:
        assert (original / name).read_bytes() == (retained / "job-a4" / name).read_bytes()
        shutil.copyfile(original / name, seed / name)
    shutil.copytree(retained / "job-a4/package", seed / "package")
    assert inventory(original / "package") == inventory(seed / "package")
    identity = read(seed / "result.json")["identity"]
    assert identity["git"] == EXECUTION and identity["request_digest"] == canonical(q)
    assert identity["identity_digest"] == canonical(
        {k: v for k, v in identity.items() if k != "identity_digest"}
    )
    for name, expected in identity["code"].items():
        assert "sha256:" + hashlib.sha256(git_bytes(EXECUTION, name)).hexdigest() == expected
        assert (ROOT / name).read_bytes() == git_bytes(CANDIDATE, name)
    for name in ("fetch.json", "result.json", "run.json"):
        record = read(seed / name)
        assert record == sealed(record) and record["identity"] == identity
    resources, attempts = read(seed / "resources.json"), read(seed / "attempts.json")
    for phase, output_names in (("fetch", ["fetch.json"]), ("screen", ["result.json", "run.json"])):
        entry = resources[phase]
        assert entry == sealed(entry) and entry["identity"] == identity
        assert (
            entry["status"] == "succeeded" and entry["exit_code"] == 0 and entry["failure"] is None
        )
        assert entry == [a for a in attempts if a["phase"] == phase][-1]
        assert entry["output_digests"] == {n: read(seed / n)["digest"] for n in output_names}
    assert read(seed / "run.json")["result_digest"] == read(seed / "result.json")["digest"]
    source = original / "source/DWD"
    assert sha(source / "manifest.json") == q["authority"]["manifest_sha256"]
    assert (source / "READY").read_text().strip() == sha(source / "manifest.json")
    manifest = read(source / "manifest.json")
    declared = {x["path"]: x for x in manifest["content"]["files"]}
    for entry in members:
        assert entry == declared[entry["path"]]
        src, dst = source / entry["path"], seed / "source/DWD" / entry["path"]
        assert sha(src) == entry["sha256"] and src.stat().st_size == entry["size_bytes"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        assert src.stat().st_ino != dst.stat().st_ino and sha(dst) == entry["sha256"]
    for name in ("manifest.json", "READY"):
        shutil.copyfile(source / name, seed / "source/DWD" / name)
    log_proof = read(retained / "final-validation.json")
    for name, expected in log_proof["logs"].items():
        assert sha(retained / name) == "sha256:" + expected
    compiled = [
        marshal.dumps(
            compile(git_bytes(ref, "src/quanthus/hf_dwd_research.py"), "worker.py", "exec")
        )
        for ref in (EXECUTION, CANDIDATE)
    ]
    assert compiled[0] == compiled[1]
    unchanged = {}
    for name in ("tests", "scripts", "ops", "config/research", "pyproject.toml", "Makefile"):
        ids = [
            subprocess.check_output(
                ["git", "rev-parse", f"{ref}:{name}"], cwd=ROOT, text=True
            ).strip()
            for ref in (EXECUTION, CANDIDATE)
        ]
        assert ids[0] == ids[1]
        unchanged[name] = ids[0]
    result = {
        "candidate": CANDIDATE,
        "execution": EXECUTION,
        "members": 56,
        "bytes": 187921175,
        "source_rows_read": False,
        "separate_physical_copy": True,
        "identity": identity,
        "compiled_identical": True,
        "compiled_sha256": hashlib.sha256(compiled[0]).hexdigest(),
        "compile_filename": "worker.py",
        "unchanged_trees": unchanged,
        "author_log_hashes_verified": len(log_proof["logs"]),
    }
    save(out / "provenance.json", result)
    return q, members, identity, seed


def fault_probes(q, members, identity, seed, out):
    results = []

    def clone(name, packaged=True):
        dest = out / "cases" / name
        dest.mkdir(parents=True)
        for p in seed.glob("*.json"):
            shutil.copyfile(p, dest / p.name)
        (dest / "source").symlink_to(seed / "source", target_is_directory=True)
        if packaged:
            shutil.copytree(seed / "package", dest / "package")
        return dest

    def rejected_publish(job):
        calls = []

        def forbidden_api(**kwargs):
            calls.append("HfApi")
            raise AssertionError("unexpected remote API construction")

        with patch.dict(
            sys.modules,
            {
                "huggingface_hub": types.SimpleNamespace(
                    HfApi=forbidden_api, CommitOperationAdd=lambda **k: k
                )
            },
        ):
            try:
                worker.publish(q, members, identity, job)
            except worker.GuardError as exc:
                reason = str(exc)
            else:
                raise AssertionError("publication accepted invalid stage")
        assert calls == []
        return reason

    damages = (
        "failed",
        "running",
        "missing",
        "identity",
        "output",
        "digest",
        "stale",
        "bool_exit",
        "failure",
        "phase",
    )
    for packaged in (False, True):
        for phase in ("fetch", "screen"):
            for damage in damages:
                name = f"{phase}-{damage}-package-{packaged}"
                job = clone(name, packaged)
                before = inventory(job / "package") if packaged else None
                resources, attempts = read(job / "resources.json"), read(job / "attempts.json")
                index = max(i for i, a in enumerate(attempts) if a["phase"] == phase)
                entry = resources[phase]
                if damage == "missing":
                    del resources[phase]
                elif damage == "stale":
                    attempts.append(sealed({**entry, "attempt": 2, "status": "running"}))
                else:
                    if damage == "failed":
                        entry.update(status="failed", exit_code=-9, failure="rss_exceeded")
                    elif damage == "running":
                        entry.update(status="running", exit_code=None)
                    elif damage == "identity":
                        entry["identity"] = {**identity, "git": "0" * 40}
                    elif damage == "output":
                        entry["output_digests"] = {"foreign.json": "sha256:" + "0" * 64}
                    elif damage == "bool_exit":
                        entry["exit_code"] = False
                    elif damage == "failure":
                        entry["failure"] = "stage_wall_exceeded"
                    elif damage == "phase":
                        entry["phase"] = "publish"
                    entry = sealed(entry)
                    if damage == "digest":
                        entry["digest"] = "sha256:" + "0" * 64
                    resources[phase], attempts[index] = entry, entry
                save(job / "resources.json", resources)
                save(job / "attempts.json", attempts)
                reason = rejected_publish(job)
                assert (
                    inventory(job / "package") == before
                    if packaged
                    else not (job / "package").exists()
                )
                if phase == "screen":
                    try:
                        worker.screen(q, members, identity, job)
                    except worker.GuardError:
                        pass
                    else:
                        raise AssertionError("cached failed screen was reused")
                results.append({"case": name, "rejected_before_HF": True, "reason": reason})

    for name in ("package", "package.staging"):
        job = clone("partial-" + name, False)
        partial = job / name
        partial.mkdir()
        (partial / "interrupted.partial").write_bytes(b"retained interruption")
        before = {n: sha(job / n) for n in ("result.json", "run.json")}
        package = worker.build_package(q, members, identity, job)
        first = inventory(package)
        worker.build_package(q, members, identity, job)
        assert inventory(package) == first and before == {n: sha(job / n) for n in before}
        kept = list((job / "package-quarantine").glob("*/interrupted.partial"))
        assert len(kept) == 1 and kept[0].read_bytes() == b"retained interruption"
        assert read(job / "package-recovery.json")[0]["reason"] == "incomplete_without_READY"
        results.append({"case": "recover-" + name, "retained_and_immutable": True})
    job = clone("ready-staging")
    (job / "package").rename(job / "package.staging")
    first = inventory(job / "package.staging")
    assert inventory(worker.build_package(q, members, identity, job)) == first
    results.append({"case": "ready-staging-promotion", "same_bytes": True})
    for name in ("package", "package.staging"):
        job = clone("damaged-ready-" + name)
        if name != "package":
            (job / "package").rename(job / name)
        path = job / name / worker.artifact_name(q, "result")
        path.write_text("corrupted")
        before = inventory(job / name)
        rejected_publish(job)
        assert before == inventory(job / name) and not (job / "package-quarantine").exists()
        results.append({"case": "damaged-ready-" + name, "rejected_preserved": True})
    job = clone("positive-existing")
    before = inventory(job / "package")
    assert inventory(worker.build_package(q, members, identity, job)) == before
    worker.screen(q, members, identity, job)
    results.append({"case": "positive-existing", "same_bytes_and_cache_reused": True})
    for outcome in ("success", "nonzero", "timeout"):
        job = clone("guardian-" + outcome, False)
        outputs = {n: (job / n).read_text() for n in ("result.json", "run.json")}
        for name in outputs:
            (job / name).unlink()
        resources = read(job / "resources.json")
        del resources["screen"]
        save(job / "resources.json", resources)
        save(
            job / "attempts.json", [a for a in read(job / "attempts.json") if a["phase"] == "fetch"]
        )
        request = copy.deepcopy(q)
        request["budget"]["max_stage_wall_seconds"]["screen"] = 1
        child = (
            "import json,time,sys;from pathlib import Path;"
            f"p=Path({str(job)!r});"
            "s=json.loads((p/'resources.json').read_text())['screen'];"
            "assert s['status']=='running' and s['attempt']==1;"
            + "".join(f"(p/{n!r}).write_text({value!r});" for n, value in outputs.items())
            + (
                "time.sleep(5)"
                if outcome == "timeout"
                else "sys.exit(7)"
                if outcome == "nonzero"
                else ""
            )
        )
        popen = subprocess.Popen
        with patch.object(worker, "load_bundle", return_value=(request, members, identity)):
            with patch.object(
                worker.subprocess,
                "Popen",
                side_effect=lambda cmd, _spawn=popen, _code=child, **kw: _spawn(
                    [sys.executable, "-c", _code], **kw
                ),
            ):
                code = worker.launch_guarded("screen", REQUEST, job, None)
            assert code == (0 if outcome == "success" else 1)
            terminal = read(job / "resources.json")["screen"]
            assert terminal == read(job / "attempts.json")[-1]
            assert terminal == sealed(terminal)
            if outcome != "success":
                rejected_publish(job)
                save(
                    job / "retry-authorization.json",
                    {
                        "identity_digest": identity["identity_digest"],
                        "cause": "audit: failed child already wrote outputs",
                    },
                )
                retry_child = (
                    "import sys,json;from pathlib import Path;"
                    f"sys.path.insert(0,{str(ROOT / 'src')!r});"
                    "from quanthus.hf_dwd_research import screen;"
                    f"screen(json.loads({json.dumps(request)!r}),json.loads({json.dumps(members)!r}),json.loads({json.dumps(identity)!r}),Path({str(job)!r}))"
                )
                with patch.object(
                    worker.subprocess,
                    "Popen",
                    side_effect=lambda cmd, _spawn=popen, _code=retry_child, **kw: _spawn(
                        [sys.executable, "-c", _code], **kw
                    ),
                ):
                    assert worker.launch_guarded("screen", REQUEST, job, None) == 1
                assert read(job / "resources.json")["screen"]["status"] == "failed"
                rejected_publish(job)
            else:
                worker.build_package(q, members, identity, job)
            assert all((job / n).read_text() == value for n, value in outputs.items())
        results.append(
            {
                "case": "guardian-" + outcome,
                "real_child": True,
                "failed_cache_retry_rejected": outcome != "success",
            }
        )
    save(out / "fault-probes.json", results)
    return results


def candidate_days(q, out):
    import duckdb

    from quanthus.hf_dwd_research_sql import SSE, TABLES, calculate

    results = []
    for left, right in ((0, 3), (2, 3), (3, 2), (3, 3), (4, 4)):
        request = copy.deepcopy(q)
        request["scope"].update(
            decision_start="2020-12-28", decision_end="2021-01-07", execution_end="2021-01-08"
        )
        request["screen"].update(
            max_unadjusted_close_return_vol20=10, unadjusted_momentum20_band=[0.00001, 10]
        )
        request["decision"].update(
            min_nominal=1,
            min_nominal_per_year=1,
            min_decision_days_per_year=3,
            min_nominal_listings=1,
            max_top_listing_share=1,
            max_top10_listing_share=1,
        )
        folder = out / "synthetic" / f"{left}-{right}"
        folder.mkdir(parents=True)
        db = duckdb.connect()
        db.execute(f"""
        CREATE TABLE dwd_trading_calendar AS
        SELECT 's'||cast(d AS DATE) calendar_session_id, '{SSE}' venue_id,
        cast(d AS DATE) trade_date,true is_open,NULL::TIMESTAMP knowledge_to
        FROM generate_series(DATE '2019-01-01',DATE '2021-01-08',INTERVAL 1 DAY) t(d)
        WHERE dayofweek(d) BETWEEN 1 AND 5;
        CREATE TABLE dwd_listing AS SELECT 'L' listing_id,'S' security_id,'{SSE}' venue_id,
        DATE '2010-01-01' effective_from,NULL::DATE effective_to,NULL::TIMESTAMP knowledge_to;
        CREATE TABLE dwd_security AS SELECT 'S' security_id,'common_equity' instrument_type,
        DATE '2010-01-01' effective_from,NULL::DATE effective_to,NULL::TIMESTAMP knowledge_to;
        CREATE TABLE prices AS SELECT *,10*exp(sum(ln(1+CASE
        WHEN trade_date BETWEEN DATE '2020-12-28' AND DATE '2020-12-31'
          AND day(trade_date)-28 < {left} THEN 0.001
        WHEN trade_date BETWEEN DATE '2021-01-04' AND DATE '2021-01-07'
          AND day(trade_date)-4 < {right} THEN 0.001 ELSE 0.05 END)) OVER(ORDER BY trade_date)) px
        FROM dwd_trading_calendar WHERE trade_date >= DATE '2020-01-02';
        CREATE TABLE dwd_security_bar AS SELECT 'L' listing_id,calendar_session_id,
        trade_date observation_start,px open,px close,2e8 turnover,'1d' interval_size,
        'UNADJUSTED' price_basis,NULL::TIMESTAMP knowledge_to FROM prices;
        CREATE TABLE dwd_tradability_state AS SELECT 'L' listing_id,calendar_session_id,
        trade_date observation_start,px*1.1 upper_price,true is_tradable,true can_buy,
        NULL::TIMESTAMP knowledge_to FROM prices;
        """)
        paths = {}
        for table in TABLES:
            p = folder / f"{table}.parquet"
            db.execute(f"COPY {table} TO '{p}' (FORMAT PARQUET)")
            paths[table] = [str(p)]
        db.close()
        db = duckdb.connect()
        result = calculate(db, request, paths)
        db.close()
        actual = [result["by_year"][str(y)]["candidate"]["decision_days"] for y in (2020, 2021)]
        assert actual == [left, right]
        assert all(result["by_year"][str(y)]["control"]["decision_days"] >= 4 for y in (2020, 2021))
        assert result["sample_sufficient"] is (min(left, right) >= 3)
        results.append(
            {"candidate_days": actual, "branch": result["decision_branch"], "pass": True}
        )
    save(out / "candidate-days.json", results)
    return results


def hf_readback(retained, out):
    from huggingface_hub import HfApi

    package = retained / "job-a4/package"
    api = HfApi()
    names = {
        n[len(PREFIX) + 1 :]
        for n in api.list_repo_files(
            "rv192/quanthus-research-r1", repo_type="dataset", revision=REVISION
        )
        if n.startswith(PREFIX + "/")
    }
    assert names == set(inventory(package)) and len(names) == 10
    hashes = {}
    for name in sorted(names):
        path = Path(
            api.hf_hub_download(
                "rv192/quanthus-research-r1",
                PREFIX + "/" + name,
                repo_type="dataset",
                revision=REVISION,
                force_download=True,
                cache_dir=str(out / "hf-cache"),
            )
        )
        assert path.read_bytes() == (package / name).read_bytes()
        hashes[name] = sha(path)
    manifest = read(package / "RESEARCH-R1-HF-002-T1-A4-manifest.json")
    assert manifest == sealed(manifest) and manifest["digest"] == PACKAGE_DIGEST
    result = {
        "revision": REVISION,
        "prefix": PREFIX,
        "files": hashes,
        "package_digest": PACKAGE_DIGEST,
    }
    save(out / "hf-readback.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("probe", "readback"))
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--retained", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    started = time.monotonic()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.mode == "probe":
        q, members, identity, seed = provenance(args.original, args.retained, args.out)
        probes = fault_probes(q, members, identity, seed, args.out)
        days = candidate_days(q, args.out)
        summary = {"fault_cases": len(probes), "sample_cases": len(days), "status": "PASS"}
    else:
        result = hf_readback(args.retained, args.out)
        summary = {"files": len(result["files"]), "status": "PASS", "revision": REVISION}
    summary.update(
        seconds=time.monotonic() - started,
        audit_git=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        candidate=CANDIDATE,
        execution=EXECUTION,
    )
    save(args.out / (args.mode + "-summary.json"), summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
