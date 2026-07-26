#!/usr/bin/env bash
# Multi-day Phase B shadow matrix soak (runbook §Shadow deployment).
# Resumable: skips steps whose latest attempt already matches expected terminal status.
# Minute collector mode is env-explicit (no auto window switch in lane_runner):
#   production default: HERMES_ODS_MINUTE_MODE=incr
#   this matrix soak:   HERMES_ODS_MINUTE_MODE=backfill + HERMES_ODS_MINUTE_MAX_PAGES=3
# Pause: touch $SHADOW/MATRIX_SOAK_PAUSE — checked between steps; current attempt is not killed.
# No timers / live cron / curated PG.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=${QUANTHUS_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}
SHADOW=${MATRIX_SHADOW_ROOT:-/root/work/astock/data/shadow/hermes-ods-v1-provider-matrix}
PRIOR_CANARY_ROOT=${HERMES_ODS_PRIOR_CANARY_ROOT:-$(dirname "$SHADOW")/hermes-ods-v1-provider-canary}
PY=${ASTOCK_PYTHON:-/root/work/astock/.venv/bin/python}
CFG=$REPO/config/schedules/hermes-ods-schedule.v1.json
CAL=${ASTOCK_CALENDAR:-/root/work/astock/data/ref_trading_calendar.parquet}
HOME_DATA=${ASTOCK_HOME:-/root/work/astock/data}
LOGDIR=$SHADOW/logs
EVID=$SHADOW/evidence
STATE=$SHADOW/MATRIX_SOAK_STATE.jsonl
PROGRESS=$SHADOW/MATRIX_SOAK_PROGRESS.json
SUMMARY=$SHADOW/MATRIX_SOAK_SUMMARY.json
PAUSE_FLAG=$SHADOW/MATRIX_SOAK_PAUSE
TMPDIR=$SHADOW/tmp
mkdir -p "$LOGDIR" "$EVID" "$SHADOW/locks" "$TMPDIR"
echo $$ > "$LOGDIR/matrix_soak.pid"
{
  date -Is
  echo "MATRIX_SOAK_START head=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown) shadow=$SHADOW pid=$$"
  echo "MINUTE_MODE=${HERMES_ODS_MINUTE_MODE:-backfill} MAX_PAGES=${HERMES_ODS_MINUTE_MAX_PAGES:-3}"
} | tee -a "$LOGDIR/matrix_soak_meta.log"

export PYTHONPATH=$REPO/src
export HERMES_ODS_ACQUIRE_MODE=provider
export HERMES_ODS_PROVIDER_SPEC=/etc/quanthus/hermes-provider-collectors.v1.json
unset HERMES_ODS_SYMBOL_LIMIT
export HERMES_ODS_MINUTE_TIMEOUT_SEC=${HERMES_ODS_MINUTE_TIMEOUT_SEC:-10800}
# Explicit archive mode for multi-day matrix (production default remains incr when unset).
export HERMES_ODS_MINUTE_MODE=${HERMES_ODS_MINUTE_MODE:-backfill}
export HERMES_ODS_MINUTE_MAX_PAGES=${HERMES_ODS_MINUTE_MAX_PAGES:-3}
export ASTOCK_HOME=$HOME_DATA
export ASTOCK_ROOT=${ASTOCK_ROOT:-/root/work/astock}
export ASTOCK_MINUTE_ROOT=${ASTOCK_MINUTE_ROOT:-/root/work/astock/minute}
export ASTOCK_MINUTE_PYTHON=${ASTOCK_MINUTE_PYTHON:-/root/work/astock/.venv-a-stock-data/bin/python}
export ASTOCK_PYTHON=$PY

OPEN_DATES=(2026-07-20 2026-07-21 2026-07-22 2026-07-23 2026-07-24)
CLOSED_WEEKDAY=2026-06-19
EVENT_DATES=(2026-07-18 2026-07-19 2026-07-20 2026-07-21 2026-07-22 2026-07-23 2026-07-24)
WEEKLY_DATES=(2026-07-18 2026-07-25)
RERUN_DATE=2026-07-22

maybe_pause() {
  if [[ -f "$PAUSE_FLAG" ]]; then
    local reason
    reason=$(cat "$PAUSE_FLAG" 2>/dev/null || true)
    {
      date -Is
      echo "MATRIX_SOAK_PAUSED reason=${reason:-flag_present} next_step_not_started"
    } | tee -a "$LOGDIR/matrix_soak_meta.log"
    $PY - "$PROGRESS" <<'PY' || true
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
p = Path(sys.argv[1])
ts = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
payload = {"updated_at": ts, "last_step": "PAUSED", "exit_code": 0, "status": "paused"}
if p.is_file():
    try:
        old = json.loads(p.read_text())
        payload["prior"] = old
    except Exception:
        pass
p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(payload, ensure_ascii=False))
PY
    echo 130 > "$LOGDIR/matrix_soak.exit"
    exit 130
  fi
}

latest_attempt_json() {
  # args: lane market_date [stream]
  local lane="$1" mdate="$2" stream="${3:-}"
  $PY - "$SHADOW" "$lane" "$mdate" "$stream" <<'PY'
import json, sys
from pathlib import Path
shadow, lane, mdate, stream = sys.argv[1:5]
base = Path(shadow) / "attempts" / lane
if stream:
    base = base / stream
base = base / mdate
if not base.is_dir():
    print("{}"); raise SystemExit(0)
cands = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name)
if not cands:
    print("{}"); raise SystemExit(0)
att = cands[-1]
out = {"attempt_dir": str(att)}
aj = att / "attempt.json"
if aj.is_file():
    a = json.loads(aj.read_text())
    out.update({
        "attempt_id": a.get("attempt_id"),
        "status": a.get("status"),
        "release_dir": a.get("release_dir"),
        "finished_at": a.get("finished_at"),
        "detail": a.get("detail"),
        "datasets": [[d.get("name"), d.get("status"), d.get("row_count")] for d in (a.get("datasets") or [])],
    })
    ready = False
    if a.get("release_dir"):
        ready = (Path(a["release_dir"]) / "READY").is_file()
    out["has_ready"] = ready
print(json.dumps(out, ensure_ascii=False))
PY
}

record() {
  # args: step expected_status exit_code extra_json_file_or_inline
  local step="$1" expected="$2" ec="$3" extra_src="${4:-}"
  local ts extra_file
  ts=$(date -Is)
  extra_file=$TMPDIR/record_extra_$$.json
  if [[ -n "$extra_src" && -f "$extra_src" ]]; then
    cp "$extra_src" "$extra_file"
  elif [[ -n "$extra_src" ]]; then
    printf '%s' "$extra_src" > "$extra_file"
  else
    echo '{}' > "$extra_file"
  fi
  $PY - "$STATE" "$PROGRESS" "$ts" "$step" "$expected" "$ec" "$extra_file" <<'PY'
import json, sys
from pathlib import Path
state, progress, ts, step, expected, ec, extra_file = sys.argv[1:8]
extra = json.loads(Path(extra_file).read_text())
rec = {
    "ts": ts,
    "step": step,
    "expected_status": expected,
    "exit_code": int(ec),
    "extra": extra,
}
Path(state).open("a").write(json.dumps(rec, ensure_ascii=False) + "\n")
Path(progress).write_text(json.dumps({"updated_at": ts, "last_step": step, "exit_code": int(ec), "status": extra.get("status")}, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(rec, ensure_ascii=False)[:500])
PY
}

run_lane() {
  # args: step lane market_date [stream] [force_preflight] [issue_ref] [timeout_sec] [expected]
  local step="$1" lane="$2" mdate="$3"
  local stream="${4:-}" force="${5:-}" issue="${6:-}" tmo="${7:-7200}" expected="${8:-ready}"
  local extra_file=$TMPDIR/${step}.extra.json
  local log=$LOGDIR/${step}.log

  maybe_pause

  # skip if already satisfied
  if [[ -z "$force" ]]; then
    latest_attempt_json "$lane" "$mdate" "$stream" > "$extra_file"
    local cur
    cur=$($PY -c "import json;print(json.load(open('$extra_file')).get('status') or '')")
    local has_ready
    has_ready=$($PY -c "import json;print(json.load(open('$extra_file')).get('has_ready'))")
    if [[ "$expected" == "ready" && "$cur" == "ready" && "$has_ready" == "True" ]]; then
      echo "SKIP $step already ready" | tee -a "$LOGDIR/matrix_soak_meta.log"
      record "$step" "$expected" 0 "$extra_file"
      return 0
    fi
    if [[ "$expected" == "closed_session" && "$cur" == "closed_session" && "$has_ready" == "True" ]]; then
      echo "SKIP $step already closed_session" | tee -a "$LOGDIR/matrix_soak_meta.log"
      record "$step" "$expected" 0 "$extra_file"
      return 0
    fi
    if [[ "$expected" == "failed" && "$cur" == "failed" && "$has_ready" != "True" ]]; then
      echo "SKIP $step already failed without READY" | tee -a "$LOGDIR/matrix_soak_meta.log"
      record "$step" "$expected" 1 "$extra_file"
      return 0
    fi
  fi

  local cmd=("$PY" -m quanthus.cli lane-run --config "$CFG" --lane "$lane" --market-date "$mdate" --home "$HOME_DATA" --shadow-root "$SHADOW" --calendar "$CAL")
  [[ -n "$stream" ]] && cmd+=(--stream "$stream")
  [[ -n "$force" ]] && cmd+=(--force-preflight "$force")
  [[ -n "$issue" ]] && cmd+=(--issue-ref "$issue")
  echo "RUN $step: ${cmd[*]} timeout=$tmo expected=$expected mode=$HERMES_ODS_MINUTE_MODE pages=$HERMES_ODS_MINUTE_MAX_PAGES" | tee -a "$LOGDIR/matrix_soak_meta.log"
  set +e
  /usr/bin/timeout "$tmo" "${cmd[@]}" >"$log" 2>&1
  local ec=$?
  set -e
  latest_attempt_json "$lane" "$mdate" "$stream" > "$extra_file"
  if [[ "$expected" == "failed" ]]; then
    $PY - "$extra_file" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
o=json.loads(p.read_text())
o["gate_no_ready"]= not bool(o.get("has_ready"))
p.write_text(json.dumps(o, ensure_ascii=False))
PY
  fi
  record "$step" "$expected" "$ec" "$extra_file"
  if ! $PY - "$extra_file" "$expected" "$ec" <<'PY'
import json, sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
expected = sys.argv[2]
exit_code = int(sys.argv[3])
status = payload.get("status")
has_ready = bool(payload.get("has_ready"))
release_dir = payload.get("release_dir")

if expected == "ready":
    ok = exit_code == 0 and status == "ready" and has_ready and release_dir
elif expected == "closed_session":
    ok = exit_code == 0 and status == "closed_session" and has_ready and release_dir
elif expected == "failed":
    ok = exit_code != 0 and status == "failed" and not has_ready and not release_dir
else:
    raise SystemExit(f"unsupported expected status: {expected}")

if not ok:
    print(
        f"matrix step mismatch: expected={expected} exit={exit_code} "
        f"status={status} has_ready={has_ready} release_dir={release_dir}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
  then
    echo "BLOCKED $step outcome mismatch" | tee -a "$LOGDIR/matrix_soak_meta.log"
    return 1
  fi
  echo "DONE $step exit=$ec" | tee -a "$LOGDIR/matrix_soak_meta.log"
  cat "$extra_file" | tee -a "$LOGDIR/matrix_soak_meta.log" >/dev/null
  return 0
}

# -------- Phase 1: market --------
for d in "${OPEN_DATES[@]}"; do
  run_lane "market_open_$d" market-daily-core "$d" "" "" "" 3600 ready
done
run_lane "market_closed_$CLOSED_WEEKDAY" market-daily-core "$CLOSED_WEEKDAY" "" "" "" 1800 closed_session
run_lane "market_missing_disagree_$RERUN_DATE" market-daily-core "$RERUN_DATE" "" "missing_or_disagree" "" 600 failed
run_lane "market_rerun_$RERUN_DATE" market-daily-core "$RERUN_DATE" "" "" "" 3600 ready

# -------- Phase 2: events 7 natural days --------
for d in "${EVENT_DATES[@]}"; do
  run_lane "events_$d" calendar-daily-events "$d" "" "" "" 3600 ready
done

# -------- Phase 3: weekly x2 --------
for d in "${WEEKLY_DATES[@]}"; do
  run_lane "weekly_$d" weekly-corrections "$d" "" "" "" 7200 ready
done

# -------- Phase 4: intraday closed/missing + open full --------
run_lane "intraday5m_closed_$CLOSED_WEEKDAY" intraday-archive "$CLOSED_WEEKDAY" "5m" "" "" 1800 closed_session
run_lane "intraday1m_closed_$CLOSED_WEEKDAY" intraday-archive "$CLOSED_WEEKDAY" "1m" "" "" 1800 closed_session
run_lane "intraday5m_missing_$RERUN_DATE" intraday-archive "$RERUN_DATE" "5m" "missing_or_disagree" "" 600 failed
run_lane "intraday1m_missing_$RERUN_DATE" intraday-archive "$RERUN_DATE" "1m" "missing_or_disagree" "" 600 failed

for d in "${OPEN_DATES[@]}"; do
  run_lane "intraday5m_open_$d" intraday-archive "$d" "5m" "" "" 12000 ready
  run_lane "intraday1m_open_$d" intraday-archive "$d" "1m" "" "" 12000 ready
done

# -------- Phase 5: bootstrap --------
run_lane "bootstrap_WS26" bootstrap "$RERUN_DATE" "" "" "WS-26/#41" 1800 failed

# -------- Phase 6: deterministic failure injection --------
maybe_pause
if [[ "${HERMES_ODS_ENABLE_FAILURE_INJECTION:-0}" != "1" ]]; then
  {
    date -Is
    echo "MATRIX_SOAK_WAITING_FAILURE_INJECTION_APPROVAL"
  } | tee -a "$LOGDIR/matrix_soak_meta.log"
  $PY - "$PROGRESS" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

path = Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
                timespec="seconds"
            ),
            "last_step": "WAITING_FAILURE_INJECTION_APPROVAL",
            "exit_code": 130,
            "status": "paused",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
PY
  echo 130 > "$LOGDIR/matrix_soak.exit"
  exit 130
fi
for stage in acquisition pre_seal transfer; do
  maybe_pause
  if $PY - "$EVID/failure_injection_${stage}.json" "$stage" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
stage=sys.argv[2]
payload=json.loads(p.read_text()) if p.is_file() else {}
ok=(
    payload.get("status")=="failed"
    and not payload.get("has_ready")
    and not payload.get("release_dir")
    and f"at {stage}" in str(payload.get("detail") or "")
)
raise SystemExit(0 if ok else 1)
PY
  then
    echo "SKIP fi_$stage evidence already valid" | tee -a "$LOGDIR/matrix_soak_meta.log"
    continue
  fi
  export HERMES_ODS_FAILURE_INJECTION_STAGE=$stage
  run_lane "fi_$stage" market-daily-core 2026-07-21 "" both_open "" 3600 failed
  latest_attempt_json market-daily-core 2026-07-21 "" > "$EVID/failure_injection_${stage}.json"
done
unset HERMES_ODS_FAILURE_INJECTION_STAGE

# -------- Final summary --------
$PY - "$SHADOW" "$PRIOR_CANARY_ROOT" <<'PY'
import json
import sys
from pathlib import Path
from quanthus.pipelines import verify_ods_release

SHADOW=Path(sys.argv[1])
PRIOR_CANARY=Path(sys.argv[2])
rows=[]
sp=SHADOW/"MATRIX_SOAK_STATE.jsonl"
if sp.is_file():
    for line in sp.read_text().splitlines():
        if line.strip() and not line.startswith('{"probe"'):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

inv=[]
for att in SHADOW.glob("attempts/**/attempt.json"):
    a=json.loads(att.read_text())
    inv.append({
        "lane": a.get("lane_id"),
        "stream": a.get("stream"),
        "market_date": a.get("market_date"),
        "attempt_id": a.get("attempt_id"),
        "status": a.get("status"),
        "has_release": a.get("release_dir") is not None,
        "has_ready": bool(a.get("release_dir") and (Path(a["release_dir"])/"READY").is_file()),
        "path": str(att.parent),
    })

wms={}
wm_root=SHADOW/"watermarks"
if wm_root.is_dir():
    for p in wm_root.glob("*.json"):
        try:
            wms[p.name]=json.loads(p.read_text())
        except Exception as e:
            wms[p.name]={"error":str(e)}

open_dates=["2026-07-20","2026-07-21","2026-07-22","2026-07-23","2026-07-24"]
event_dates=["2026-07-18","2026-07-19","2026-07-20","2026-07-21","2026-07-22","2026-07-23","2026-07-24"]
weekly_dates=["2026-07-18","2026-07-25"]
closed="2026-06-19"

def ready_attempt(lane, mdate, stream=None, status="ready"):
    c=[x for x in inv if x["lane"]==lane and x["market_date"]==mdate and x.get("stream")==stream]
    c=[x for x in c if x["status"]==status and x["has_ready"] and x["has_release"]]
    return sorted(c, key=lambda x: x["attempt_id"] or "")[-1] if c else None

gates={
  "market_open_ready": {d: bool(ready_attempt("market-daily-core", d)) for d in open_dates},
  "market_closed": bool(ready_attempt("market-daily-core", closed, status="closed_session")),
  "events": {d: bool(ready_attempt("calendar-daily-events", d)) for d in event_dates},
  "weekly": {d: bool(ready_attempt("weekly-corrections", d)) for d in weekly_dates},
  "intraday_5m_open": {d: bool(ready_attempt("intraday-archive", d, "5m")) for d in open_dates},
  "intraday_1m_open": {d: bool(ready_attempt("intraday-archive", d, "1m")) for d in open_dates},
  "intraday_5m_closed": bool(ready_attempt("intraday-archive", closed, "5m", "closed_session")),
  "intraday_1m_closed": bool(ready_attempt("intraday-archive", closed, "1m", "closed_session")),
}

ok_market=all(gates["market_open_ready"].values())
ok_events=all(gates["events"].values())
ok_weekly=all(gates["weekly"].values())
ok_5m=all(gates["intraday_5m_open"].values())
ok_1m=all(gates["intraday_1m_open"].values())
ok_closed_m=gates["market_closed"]
ok_closed_5=gates["intraday_5m_closed"]
ok_closed_1=gates["intraday_1m_closed"]

miss=[r for r in rows if "missing" in r.get("step","")]
miss_ok=all(not bool((r.get("extra") or {}).get("has_ready")) for r in miss) if miss else False

m22=[x for x in inv if x["lane"]=="market-daily-core" and x["market_date"]=="2026-07-22" and x["status"]=="ready"]
rerun_ok=len({x["attempt_id"] for x in m22})>=2

fi_checks={}
for stage in ("acquisition", "pre_seal", "transfer"):
    p=SHADOW/"evidence"/f"failure_injection_{stage}.json"
    payload=json.loads(p.read_text()) if p.is_file() else {}
    fi_checks[stage]=bool(
        payload.get("status")=="failed"
        and not payload.get("has_ready")
        and not payload.get("release_dir")
        and f"at {stage}" in str(payload.get("detail") or "")
    )
fi_ok=all(fi_checks.values())

bootstrap_rows=[r for r in rows if r.get("step")=="bootstrap_WS26"]
bootstrap_ok=bool(bootstrap_rows) and any(
    r.get("expected_status")=="failed"
    and (r.get("extra") or {}).get("status")=="failed"
    and not (r.get("extra") or {}).get("has_ready")
    for r in bootstrap_rows
)

release_verification={}
for item in inv:
    release_dir=item.get("path") and item.get("has_release") and item.get("has_ready") and json.loads((Path(item["path"])/"attempt.json").read_text()).get("release_dir")
    if not release_dir or release_dir in release_verification:
        continue
    try:
        release_verification[release_dir]={"ok": True, **verify_ods_release(Path(release_dir)).to_dict()}
    except Exception as exc:
        release_verification[release_dir]={"ok": False, "error": str(exc)}
releases_ok=bool(release_verification) and all(x["ok"] for x in release_verification.values())

weekly_counts={name: 0 for name in ("same", "missing_in_daily", "changed_upstream", "conflict")}
weekly_independent=True
for d in weekly_dates:
    item=ready_attempt("weekly-corrections", d)
    if not item:
        continue
    attempt=json.loads((Path(item["path"])/"attempt.json").read_text())
    release=Path(attempt["release_dir"])
    manifest=json.loads((release/"release.json").read_text())
    artifact=next((x for x in manifest["artifacts"] if x["name"]=="reconciliation_summary"), None)
    if not artifact or "/weekly/" not in release.as_posix():
        weekly_independent=False
        continue
    payload=json.loads((release/artifact["relative_path"]).read_text())
    weekly_independent = weekly_independent and payload.get("may_overwrite_daily") is False
    for name in weekly_counts:
        weekly_counts[name] += int((payload.get("counts") or {}).get(name, 0))
weekly_four_classes=weekly_independent and all(value > 0 for value in weekly_counts.values())

minute_diff_path=SHADOW/"evidence"/"minute_legacy_coverage_diff.json"
minute_diff=json.loads(minute_diff_path.read_text()) if minute_diff_path.is_file() else {}
minute_diff_ok=bool(
    minute_diff.get("coverage_ok") is True
    and minute_diff.get("legacy_source")
    and all(
        (minute_diff.get("streams") or {}).get(stream, {}).get("unexplained_missing_count")==0
        for stream in ("1m", "5m")
    )
)

wm_1m = any("1m" in k for k in wms)
wm_5m = any("5m" in k for k in wms)

def prior_ready(stream):
    for path in PRIOR_CANARY.glob(f"attempts/intraday-archive/{stream}/*/*/attempt.json"):
        payload=json.loads(path.read_text())
        release_dir=payload.get("release_dir")
        if payload.get("status")=="ready" and release_dir and (Path(release_dir)/"READY").is_file():
            return True
    return False

prior = {
  "root": str(PRIOR_CANARY),
  "5m_ready": prior_ready("5m"),
  "1m_ready": prior_ready("1m"),
}

all_green = all([
    ok_market, ok_events, ok_weekly, ok_5m, ok_1m,
    ok_closed_m, ok_closed_5, ok_closed_1, miss_ok, rerun_ok,
    fi_ok, bootstrap_ok, releases_ok, weekly_four_classes,
    minute_diff_ok, wm_1m, wm_5m,
])

summary={
  "producer": "hermes-ods-phase-b-matrix-soak",
  "shadow_root": str(SHADOW),
  "minute_mode_env": {
    "HERMES_ODS_MINUTE_MODE": "backfill (soak default)",
    "HERMES_ODS_MINUTE_MAX_PAGES": "3 (soak default)",
    "production_default": "incr when MODE unset",
  },
  "steps_recorded": len(rows),
  "attempts": inv,
  "watermarks": wms,
  "release_verification": release_verification,
  "weekly_correction_counts": weekly_counts,
  "failure_injection": fi_checks,
  "minute_legacy_coverage_diff": minute_diff,
  "gates": gates,
  "prior_single_day_canary": prior,
  "checks": {
    "market_open_5_ready": ok_market,
    "market_closed_session": ok_closed_m,
    "events_7_ready": ok_events,
    "weekly_2_ready": ok_weekly,
    "intraday_5m_open_5_ready": ok_5m,
    "intraday_1m_open_5_ready": ok_1m,
    "intraday_closed_session": ok_closed_5 and ok_closed_1,
    "missing_disagree_no_ready": miss_ok,
    "market_rerun_distinct_attempts": rerun_ok,
    "failure_injection_three_stages": fi_ok,
    "bootstrap_refused_without_release": bootstrap_ok,
    "all_ready_releases_verified": releases_ok,
    "weekly_four_classes_and_independent": weekly_four_classes,
    "minute_legacy_coverage_diff": minute_diff_ok,
    "wm_1m_present": wm_1m,
    "wm_5m_present": wm_5m,
  },
  "all_green": all_green,
  "decision": "READY_FOR_OCI_PHASE_B" if all_green else "PHASE_B_MATRIX_RESIDUAL",
}
(SHADOW/"MATRIX_SOAK_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
print(json.dumps({"all_green": all_green, "decision": summary["decision"], "checks": summary["checks"]}, ensure_ascii=False, indent=2))
PY

if [[ "$($PY -c 'import json,sys; print(json.load(open(sys.argv[1])).get("all_green") is True)' "$SUMMARY")" != "True" ]]; then
  echo "MATRIX_SOAK_RESIDUAL $(date -Is)" | tee -a "$LOGDIR/matrix_soak_meta.log"
  echo 4 > "$LOGDIR/matrix_soak.exit"
  exit 4
fi

echo "MATRIX_SOAK_DONE $(date -Is)" | tee -a "$LOGDIR/matrix_soak_meta.log"
echo 0 > "$LOGDIR/matrix_soak.exit"
exit 0
