"""Bounded input-only industry coverage inspection for P3-STRAT-003-B-R1."""
from __future__ import annotations

import argparse, hashlib, json, os, re, signal
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from quanthus.event_library.physical_scan import _read_parquet_expression, _table_paths
from quanthus.expert_evaluation.serialization import ExpertEvaluationError, canonical_json_bytes, digest_value
from quanthus.expert_strategy.p3_strat_003_input_qualification import _request
from quanthus.forward_validation.activation import load_prospective_runtime_config
from quanthus.industry_mapping import IndustryMappingError, load_static_industry_mapping
from quanthus.policy import find_project_root

REQUEST_SCHEMA = "quanthus.p3-strat-003-industry-inspection.v1"
REQUEST_DIGEST = "sha256:3262e61bf8cfab5e58f049a5215dedce319ec71c6d2ec2a6855e43562364d57c"
ARTIFACT_SCHEMA = "quanthus.p3-strat-003-industry-inspection-artifact.v1"
COMPLETE, UNAVAILABLE = "INDUSTRY_COVERAGE_INSPECTION_COMPLETE", "DIAGNOSTIC_INPUT_UNAVAILABLE"
SHA, GIT = re.compile(r"^sha256:[0-9a-f]{64}$"), re.compile(r"^[0-9a-f]{40}$")


def _fail(msg: str) -> NoReturn:
    raise ExpertEvaluationError("p3_strat_003_industry_inspection_invalid", msg)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return "sha256:" + h.hexdigest()


def _obj(path: Path, root: Path, limit: int, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        _fail(f"{label} escapes fixed root")
    try:
        if path.stat().st_size > limit:
            _fail(f"{label} exceeds limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError("p3_strat_003_industry_inspection_unreadable", label) from exc
    if not isinstance(value, dict):
        _fail(f"{label} must be object")
    return value


def _run_root() -> Path:
    return Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs")).resolve()


def _candidate(root: Path, failed: dict[str, Any], start: date, end: date) -> tuple[dict[str, Any], list[dict[str, str]]]:
    rel = Path(str(failed["candidate_relative_path"]))
    if rel.is_absolute() or ".." in rel.parts:
        _fail("candidate path invalid")
    path = (root / ".staging" / failed["job_id"] / str(failed["claim_generation"]) / rel).resolve()
    if not path.is_file() or path.is_symlink() or not path.is_relative_to(root):
        return {"status": "missing_or_unreadable", "path": str(path)}, []
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ExpertEvaluationError("dependency_missing", "duckdb") from exc
    con = duckdb.connect(":memory:")
    try:
        cols = {str(r[0]) for r in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()}
        req = {"dwd_security_id", "dwd_company_id", "dwd_listing_id", "listing_code", "venue_id", "session_date"}
        if not req.issubset(cols):
            _fail("candidate identity columns missing")
        bad = con.execute("""SELECT count(*) FROM read_parquet(?) WHERE dwd_security_id IS NULL OR dwd_company_id IS NULL OR listing_code IS NULL OR venue_id IS NULL OR session_date IS NULL""", [str(path)]).fetchone()
        dup = con.execute("""SELECT count(*) FROM (SELECT dwd_security_id,session_date FROM read_parquet(?) GROUP BY ALL HAVING count(*)<>1)""", [str(path)]).fetchone()
        if bad is None or dup is None or int(bad[0]) or int(dup[0]):
            _fail("candidate identity rows invalid")
        totals = con.execute("""SELECT count(*),count(DISTINCT dwd_security_id),min(session_date),max(session_date),count(DISTINCT dwd_security_id) FILTER(WHERE session_date BETWEEN ? AND ?) FROM read_parquet(?)""", [start, end, str(path)]).fetchone()
        rows = con.execute("""SELECT dwd_security_id,dwd_company_id,min(listing_code),min(venue_id),min(session_date),max(session_date),max(CASE WHEN session_date BETWEEN ? AND ? THEN 1 ELSE 0 END) FROM read_parquet(?) GROUP BY dwd_security_id,dwd_company_id ORDER BY dwd_security_id""", [start, end, str(path)]).fetchall()
    finally:
        con.close()
    if totals is None:
        _fail("candidate inventory missing")
    ids = [{"security_id":str(a),"company_id":str(b),"listing_code":str(c),"venue_id":str(d),"first":e.isoformat(),"last":f.isoformat(),"decision":bool(g)} for a,b,c,d,e,f,g in rows]
    return {"status":"observed_after_failure","path":str(path),"size_bytes":path.stat().st_size,"sha256":_sha(path),"claim_execution_time_bytes_reproduced":False,"row_count":int(totals[0]),"security_count":int(totals[1]),"decision_security_count":int(totals[4]),"first_session":totals[2].isoformat(),"last_session":totals[3].isoformat()}, ids


def _p4(root: Path, ref: dict[str, Any], limit: int) -> tuple[dict[str, Any], dict[str, tuple[str,str,str]], dict[tuple[str,str], list[str]]]:
    expected = str(ref["event_manifest_sha256"])
    if not SHA.fullmatch(expected):
        _fail("P4 event binding invalid")
    receipts, raw_map, first_sha = [], None, None
    for run_id in ref["run_ids"]:
        eroot = (root / "runs" / str(run_id) / "event-library").resolve()
        em = eroot / "manifest.json"
        event = _obj(em, eroot, limit, "P4 event manifest")
        if _sha(em) != expected or not isinstance(event.get("adm"), dict):
            _fail("P4 event identity differs")
        aroot, ap = eroot / "adm", eroot / "adm/adm.json"
        adm = _obj(ap, aroot, limit, "P4 ADM")
        if _sha(ap) != event["adm"].get("manifest_sha256"):
            _fail("P4 ADM binding differs")
        ind, src = adm.get("static_industry_projection"), adm.get("static_industry_source_receipt")
        if not isinstance(ind, dict) or not isinstance(src, dict):
            _fail("P4 industry binding missing")
        mp, sp = aroot / str(ind["path"]), aroot / str(src["path"])
        if _sha(mp) != ind.get("sha256") or _sha(sp) != src.get("sha256"):
            _fail("P4 industry asset SHA differs")
        try:
            mapping = load_static_industry_mapping(mp, expected_version=str(ind["mapping_version"]))
        except IndustryMappingError as exc:
            raise ExpertEvaluationError("p3_strat_003_industry_inspection_invalid", "P4 mapping invalid") from exc
        current_sha = _sha(mp)
        if first_sha is not None and current_sha != first_sha:
            _fail("P4 A/B mapping differs")
        first_sha = current_sha
        if raw_map is None:
            raw_map = _obj(mp, aroot, int(ref["maximum_mapping_bytes"]), "P4 mapping")
        receipts.append({"run_id":str(run_id),"event_manifest_sha256":expected,"adm_manifest_sha256":_sha(ap),"mapping_sha256":current_sha,"mapping_artifact_digest":mapping.artifact_digest,"membership_count":len(mapping.memberships),"source_receipt_sha256":_sha(sp)})
    if raw_map is None:
        _fail("P4 mapping unavailable")
    exact, by_code = {}, {}
    for row in raw_map.get("memberships", []):
        if not isinstance(row, dict):
            _fail("P4 membership invalid")
        sid, code, venue, ind = map(str, (row.get("security_id") or "", row.get("stock_code") or "", row.get("exchange") or "", row.get("level1_industry_id") or ""))
        if not all((sid,code,venue,ind)):
            _fail("P4 membership incomplete")
        exact[sid] = (ind,code,venue)
        by_code.setdefault((code,venue), []).append(sid)
    return {"runs":receipts}, exact, by_code


def _dwd(base: Path, ids: list[dict[str,str]]) -> tuple[dict[str,str], dict[str,str]]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ExpertEvaluationError("dependency_missing", "duckdb") from exc
    me, ne = _read_parquet_expression(_table_paths(base,"dwd_company_industry_membership")), _read_parquet_expression(_table_paths(base,"dwd_industry_node"))
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE c(security_id VARCHAR,company_id VARCHAR)")
        con.executemany("INSERT INTO c VALUES (?,?)", [(x["security_id"],x["company_id"]) for x in ids])
        rows = con.execute(f"""WITH m AS (SELECT company_id,industry_node_id FROM {me} WHERE knowledge_to IS NULL AND membership_type='PRIMARY_CLASSIFICATION'),n AS (SELECT industry_node_id,parent_node_id,node_level FROM {ne} WHERE knowledge_to IS NULL),r AS (SELECT c.security_id,count(DISTINCT m.industry_node_id) mc,count(DISTINCT n.industry_node_id) nc,count(DISTINCT CASE WHEN n.node_level=1 THEN n.industry_node_id WHEN n.node_level=2 AND p.node_level=1 THEN p.industry_node_id END) lc,min(CASE WHEN n.node_level=1 THEN n.industry_node_id WHEN n.node_level=2 AND p.node_level=1 THEN p.industry_node_id END) li FROM c LEFT JOIN m ON m.company_id=c.company_id LEFT JOIN n ON n.industry_node_id=m.industry_node_id LEFT JOIN n p ON p.industry_node_id=n.parent_node_id GROUP BY c.security_id) SELECT * FROM r ORDER BY security_id""").fetchall()
    finally:
        con.close()
    mapping, reasons = {}, {}
    for sid,mc,nc,lc,li in rows:
        sid=str(sid)
        if int(lc)==1 and li is not None: mapping[sid],reason=str(li),"covered"
        elif int(lc)>1: reason="ambiguous_level1_membership"
        elif int(mc)==0: reason="no_company_industry_membership"
        elif int(nc)==0: reason="industry_node_missing"
        else: reason="level1_parent_unresolved"
        reasons[sid]=reason
    return mapping,reasons


def _old(runtime: Any) -> tuple[dict[str,str], dict[str,Any]]:
    try:
        m=load_static_industry_mapping(runtime.static_industry_mapping,expected_version="static-level1-concentration-research-v1")
    except IndustryMappingError as exc:
        raise ExpertEvaluationError("p3_strat_003_industry_inspection_invalid","legacy mapping invalid") from exc
    sha=_sha(runtime.static_industry_mapping)
    if sha!=runtime.static_industry_mapping_sha256: _fail("legacy mapping SHA differs")
    return {k:v.level1_industry_id for k,v in m.by_security_id.items()},{"path":str(runtime.static_industry_mapping),"sha256":sha,"membership_count":len(m.memberships),"artifact_digest":m.artifact_digest}


def _coverage(ids:list[dict[str,str]],old:dict[str,str],dwd:dict[str,str],reasons:dict[str,str],p4:dict[str,tuple[str,str,str]],by_code:dict[tuple[str,str],list[str]])->dict[str,Any]:
    allc,dec=Counter(),Counter(); unresolved=[]; conflict=[]; code_only=0
    for x in ids:
        sid=x["security_id"]; pv=p4.get(sid); dv=dwd.get(sid)
        if dv is not None: status="dwd_native"; val=dv
        elif pv is not None: status="p4_sealed_reference"; val=pv[0]
        else: status=reasons.get(sid,"unclassified"); val=None
        allc[status]+=1
        if x["decision"]: dec[status]+=1
        if dv is not None and pv is not None and dv!=pv[0]: conflict.append({**x,"dwd_level1":dv,"p4_level1":pv[0]})
        if val is None:
            matches=by_code.get((x["listing_code"],x["venue_id"]),[])
            if len(matches)==1 and matches[0]!=sid: code_only+=1
            unresolved.append({**x,"dwd_reason":reasons.get(sid),"legacy_p3_exact":sid in old})
    return {"all_security_status_counts":dict(sorted(allc.items())),"decision_security_status_counts":dict(sorted(dec.items())),"legacy_p3_exact_security_count":sum(x["security_id"] in old for x in ids),"remaining_unresolved_security_count":len(unresolved),"dwd_p4_conflict_count":len(conflict),"code_venue_only_reference_match_count":code_only,"remaining_unresolved_samples":unresolved[:20],"conflict_samples":conflict[:20]}


def build_industry_inspection(*,request_path:Path,output:Path,code_revision:str)->dict[str,Any]:
    if not GIT.fullmatch(code_revision): _fail("code revision invalid")
    project=find_project_root(request_path.resolve())
    raw=_obj(request_path.resolve(),project,262144,"request")
    if raw.get("schema_version")!=REQUEST_SCHEMA or raw.get("work_item")!="P3-STRAT-003-B-R1" or digest_value(raw)!=REQUEST_DIGEST: _fail("request differs from frozen contract")
    qpath=(project/str(raw["qualification"]["path"])).resolve()
    if _sha(qpath)!=raw["qualification"]["file_sha256"]: _fail("B-v2 request differs")
    q=_request(qpath); runtime=load_prospective_runtime_config(project/str(q["source_runtime"]["path"]),root=project)
    root=_run_root(); output=output.resolve()
    if not output.is_relative_to((root/".staging").resolve()): _fail("output must stay in event staging")
    start,end=date.fromisoformat(raw["scope"]["decision_start"]),date.fromisoformat(raw["scope"]["decision_end"])
    cand,ids=_candidate(root,raw["failed_job"],start,end)
    if not ids:
        core={"disposition":UNAVAILABLE,"failed_job":raw["failed_job"],"candidate_projection":cand}
    else:
        try:
            old,oldr=_old(runtime); p4r,p4,bycode=_p4(root,raw["p4_reference"],int(raw["limits"]["maximum_json_bytes"])); dwd,reasons=_dwd(runtime.historical_base_root.resolve(),ids)
        except ExpertEvaluationError as exc:
            core={"disposition":UNAVAILABLE,"failed_job":raw["failed_job"],"candidate_projection":cand,"industry_sources":{"status":"unavailable_or_invalid","reason_code":exc.reason_code,"detail":str(exc)[:300]}}
        else:
            core={"disposition":COMPLETE,"failed_job":raw["failed_job"],"candidate_projection":cand,"legacy_p3_mapping":oldr,"dwd_reason_counts":dict(sorted(Counter(reasons.values()).items())),"p4_reference":p4r,"coverage":_coverage(ids,old,dwd,reasons,p4,bycode)}
    core.update({"claim_failed_job_artifact_reproduced":False,"strategy_or_economic_results_read":False,"target_outcome_labels_read":False,"p6_mutation":False})
    summary={"schema_version":"quanthus.p3-strat-003-industry-inspection-summary.v1","work_item":"P3-STRAT-003-B-R1","code_revision":code_revision,"request_digest":REQUEST_DIGEST,"observed_at":datetime.now(timezone.utc).isoformat(),"inspection":core}
    sd=canonical_json_bytes(summary)+b"\n"
    if len(sd)>int(raw["limits"]["maximum_output_bytes"]): _fail("summary exceeds output limit")
    payload={"schema_version":ARTIFACT_SCHEMA,"work_item":"P3-STRAT-003-B-R1","code_revision":code_revision,"request_digest":REQUEST_DIGEST,"disposition":core["disposition"],"row_count":0,"p2_probe_count":0,"p2_fill_count":0,"candidate_event_count":0,"execution_authorized":False,"strategy_result_produced":False,"outcome_labels_produced":False,"p6_mutation":False,"b_acceptance":False,"inspection":core,"files":{"summary":{"path":"summary.json","size_bytes":len(sd),"sha256":"sha256:"+hashlib.sha256(sd).hexdigest()}}}
    manifest={**payload,"result_digest":digest_value(payload)}; md=canonical_json_bytes(manifest)+b"\n"; ready=("sha256:"+hashlib.sha256(md).hexdigest()+"\n").encode("ascii")
    output.mkdir(parents=True); (output/"summary.json").write_bytes(sd); (output/"manifest.json").write_bytes(md); (output/"READY").write_bytes(ready)
    return verify_industry_inspection_artifact(output,code_revision=code_revision,request_digest=REQUEST_DIGEST)


def verify_industry_inspection_artifact(root:Path,*,code_revision:str,request_digest:str)->dict[str,Any]:
    root=root.resolve(); m=_obj(root/"manifest.json",root,262144,"manifest"); s=_obj(root/"summary.json",root,262144,"summary"); rp=root/"READY"
    if rp.is_symlink() or not rp.resolve().is_relative_to(root): _fail("READY escapes root")
    ms=_sha(root/"manifest.json"); payload={k:v for k,v in m.items() if k!="result_digest"}; expected={"path":"summary.json","size_bytes":(root/"summary.json").stat().st_size,"sha256":_sha(root/"summary.json")}
    if rp.read_text(encoding="ascii").strip()!=ms or m.get("schema_version")!=ARTIFACT_SCHEMA or m.get("code_revision")!=code_revision or m.get("request_digest")!=request_digest or m.get("result_digest")!=digest_value(payload) or m.get("files")!={"summary":expected} or m.get("inspection")!=s.get("inspection") or m.get("disposition")!=m.get("inspection",{}).get("disposition") or any(m.get(k) is not False for k in ("execution_authorized","strategy_result_produced","outcome_labels_produced","p6_mutation","b_acceptance")): _fail("sealed artifact boundary differs")
    result={"artifact_ref":str(root),"manifest_sha256":ms,"result_digest":m["result_digest"],"request_digest":request_digest,"disposition":m["disposition"],"row_count":0,"p2_probe_count":0,"p2_fill_count":0,"candidate_event_count":0,"execution_authorized":False,"strategy_result_produced":False,"outcome_labels_produced":False,"p6_mutation":False,"b_acceptance":False}
    cov=m["inspection"].get("coverage")
    if isinstance(cov,dict): result.update({"remaining_unresolved_security_count":int(cov["remaining_unresolved_security_count"]),"dwd_p4_conflict_count":int(cov["dwd_p4_conflict_count"])})
    return result


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(); p.add_argument("--request",type=Path,required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--code-revision",required=True); a=p.parse_args(argv)
    signal.alarm(120); r=build_industry_inspection(request_path=a.request,output=a.output,code_revision=a.code_revision); signal.alarm(0); print(json.dumps(r,ensure_ascii=True,sort_keys=True,separators=(",",":"))); return 0

if __name__=="__main__": raise SystemExit(main())
