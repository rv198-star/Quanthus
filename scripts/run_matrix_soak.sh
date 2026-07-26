#!/usr/bin/env bash
# Multi-day Phase B shadow matrix soak (runbook §Shadow deployment).
# Resumable: skips steps whose latest attempt already matches expected terminal status.
# Minute collector mode is env-explicit (no auto window switch in lane_runner):
#   production default: HERMES_ODS_MINUTE_MODE=incr
#   this matrix soak:   HERMES_ODS_MINUTE_MODE=backfill + HERMES_ODS_MINUTE_MAX_PAGES=3
# Pause: touch $SHADOW/MATRIX_SOAK_PAUSE — checked between steps; current attempt is not killed.
# No timers / live cron / curated PG.
set -uo pipefail

REPO=${QUANTHUS_REPO:-/root/multica_workspaces/08cadd44-f7d8-430d-b7ac-7a1560403fa2/24dcc49a/workdir/Quanthus}
SHADOW=${MATRIX_SHADOW_ROOT:-/root/work/astock/data/shadow/hermes-ods-v1-provider-matrix}
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

clear_locks() { rm -f "$SHADOW"/locks/*.lock 2>/dev/null || true; }

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
    if [[ "$expected" == "closed_session" && "$cur" == "closed_session" ]]; then
      echo "SKIP $step already closed_session" | tee -a "$LOGDIR/matrix_soak_meta.log"
      record "$step" "$expected" 0 "$extra_file"
      return 0
    fi
  fi

  clear_locks
  local cmd=("$PY" -m quanthus.cli lane-run --config "$CFG" --lane "$lane" --market-date "$mdate" --home "$HOME_DATA" --shadow-root "$SHADOW" --calendar "$CAL")
  [[ -n "$stream" ]] && cmd+=(--stream "$stream")
  [[ -n "$force" ]] && cmd+=(--force-preflight "$force")
  [[ -n "$issue" ]] && cmd+=(--issue-ref "$issue")
  echo "RUN $step: ${cmd[*]} timeout=$tmo expected=$expected mode=$HERMES_ODS_MINUTE_MODE pages=$HERMES_ODS_MINUTE_MAX_PAGES" | tee -a "$LOGDIR/matrix_soak_meta.log"
  set +e
  /usr/bin/timeout "$tmo" "${cmd[@]}" >"$log" 2>&1
  local ec=$?
  set +e
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
run_lane "bootstrap_WS26" bootstrap "$RERUN_DATE" "" "" "WS-26/#41" 1800 ready

# -------- Phase 6: failure injection --------
maybe_pause
clear_locks
FI_LOG=$LOGDIR/fi_market_interrupt.log
echo "FI start market interrupt" | tee -a "$LOGDIR/matrix_soak_meta.log"
$PY -m quanthus.cli lane-run --config "$CFG" --lane market-daily-core --market-date 2026-07-21 \
  --home "$HOME_DATA" --shadow-root "$SHADOW" --calendar "$CAL" >"$FI_LOG" 2>&1 &
FI_PID=$!
sleep 8
FI_ATT=$($PY - <<'PY'
from pathlib import Path
base=Path("/root/work/astock/data/shadow/hermes-ods-v1-provider-matrix/attempts/market-daily-core/2026-07-21")
cands=sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name) if base.is_dir() else []
print(str(cands[-1]) if cands else "")
PY
)
KILL_TS=$(date -Is)
kill -TERM "$FI_PID" 2>/dev/null || true
sleep 2
kill -KILL "$FI_PID" 2>/dev/null || true
wait "$FI_PID" 2>/dev/null || true
$PY - <<PY
import json
from pathlib import Path
att=Path("$FI_ATT") if "$FI_ATT" else None
payload={
  "kind": "failure_injection_market_acquisition_interrupt",
  "recorded_at": "$KILL_TS",
  "pid": $FI_PID,
  "signal": "SIGTERM+SIGKILL",
  "attempt_dir": str(att) if att else None,
}
if att and (att/"attempt.json").is_file():
  a=json.loads((att/"attempt.json").read_text())
  payload["attempt_id"]=a.get("attempt_id")
  payload["last_status"]=a.get("status")
  payload["release_dir"]=a.get("release_dir")
  payload["has_ready"]=bool(a.get("release_dir") and (Path(a["release_dir"])/"READY").is_file())
  payload["no_ready"]=not payload["has_ready"]
  payload["no_release"]=a.get("release_dir") is None
Path("$EVID/failure_injection_market_interrupt.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
print(json.dumps(payload, ensure_ascii=False))
PY
record "fi_market_interrupt" "no_ready" 0 "$EVID/failure_injection_market_interrupt.json"

maybe_pause
clear_locks
FI2_LOG=$LOGDIR/fi_market_transfer_interrupt.log
$PY -m quanthus.cli lane-run --config "$CFG" --lane market-daily-core --market-date 2026-07-20 \
  --home "$HOME_DATA" --shadow-root "$SHADOW" --calendar "$CAL" >"$FI2_LOG" 2>&1 &
FI2_PID=$!
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 12
  if ! kill -0 "$FI2_PID" 2>/dev/null; then break; fi
done
KILL2_TS=$(date -Is)
if kill -0 "$FI2_PID" 2>/dev/null; then
  kill -TERM "$FI2_PID" 2>/dev/null || true
  sleep 2
  kill -KILL "$FI2_PID" 2>/dev/null || true
fi
wait "$FI2_PID" 2>/dev/null || true
$PY - <<PY
import json
from pathlib import Path
base=Path("/root/work/astock/data/shadow/hermes-ods-v1-provider-matrix/attempts/market-daily-core/2026-07-20")
cands=sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name) if base.is_dir() else []
att=cands[-1] if cands else None
payload={"kind":"failure_injection_market_transfer_or_midrun_interrupt","recorded_at":"$KILL2_TS","pid":$FI2_PID,"signal":"SIGTERM+SIGKILL","attempt_dir":str(att) if att else None}
if att and (att/"attempt.json").is_file():
  a=json.loads((att/"attempt.json").read_text())
  payload.update({
    "attempt_id": a.get("attempt_id"),
    "last_status": a.get("status"),
    "release_dir": a.get("release_dir"),
    "has_ready": bool(a.get("release_dir") and (Path(a["release_dir"])/"READY").is_file()),
  })
  payload["no_ready_if_incomplete"] = (a.get("status")!="ready") or (not payload["has_ready"])
Path("$EVID/failure_injection_market_transfer_interrupt.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
print(json.dumps(payload, ensure_ascii=False))
PY
record "fi_market_transfer_interrupt" "no_ready_or_complete_ok" 0 "$EVID/failure_injection_market_transfer_interrupt.json"

# -------- Final summary --------
$PY - <<'PY'
import json
from pathlib import Path

SHADOW=Path("/root/work/astock/data/shadow/hermes-ods-v1-provider-matrix")
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

def latest(lane, mdate, stream=None):
    c=[x for x in inv if x["lane"]==lane and x["market_date"]==mdate and x.get("stream")==stream]
    c=sorted(c, key=lambda x: x["attempt_id"] or "")
    return c[-1] if c else None

gates={
  "market_open_ready": {d: (latest("market-daily-core", d) or {}).get("status") for d in open_dates},
  "market_closed": (latest("market-daily-core", closed) or {}),
  "events": {d: (latest("calendar-daily-events", d) or {}).get("status") for d in event_dates},
  "weekly": {d: (latest("weekly-corrections", d) or {}).get("status") for d in weekly_dates},
  "intraday_5m_open": {d: (latest("intraday-archive", d, "5m") or {}).get("status") for d in open_dates},
  "intraday_1m_open": {d: (latest("intraday-archive", d, "1m") or {}).get("status") for d in open_dates},
  "intraday_5m_closed": (latest("intraday-archive", closed, "5m") or {}),
  "intraday_1m_closed": (latest("intraday-archive", closed, "1m") or {}),
}

ok_market=all(gates["market_open_ready"].get(d)=="ready" for d in open_dates)
ok_events=all(gates["events"].get(d)=="ready" for d in event_dates)
ok_weekly=all(gates["weekly"].get(d)=="ready" for d in weekly_dates)
ok_5m=all(gates["intraday_5m_open"].get(d)=="ready" for d in open_dates)
ok_1m=all(gates["intraday_1m_open"].get(d)=="ready" for d in open_dates)
ok_closed_m=(gates["market_closed"].get("status")=="closed_session")
ok_closed_5=(gates["intraday_5m_closed"].get("status")=="closed_session")
ok_closed_1=(gates["intraday_1m_closed"].get("status")=="closed_session")

miss=[r for r in rows if "missing" in r.get("step","")]
miss_ok=all(not bool((r.get("extra") or {}).get("has_ready")) for r in miss) if miss else False

m22=[x for x in inv if x["lane"]=="market-daily-core" and x["market_date"]=="2026-07-22" and x["status"]=="ready"]
rerun_ok=len({x["attempt_id"] for x in m22})>=2

fi_files=list((SHADOW/"evidence").glob("failure_injection_*.json"))
fi_ok=len(fi_files)>=1

wm_1m = any("1m" in k for k in wms)
wm_5m = any("5m" in k for k in wms)

prior_canary = Path("/root/work/astock/data/shadow/hermes-ods-v1-provider-canary")
prior = {
  "5m_ready": (prior_canary/"attempts/intraday-archive/5m/2026-07-22/20260722-20260726T025242247561Z/attempt.json").is_file(),
  "1m_ready": (prior_canary/"attempts/intraday-archive/1m/2026-07-22/20260722-20260726T041939638227Z/attempt.json").is_file(),
}

all_green = all([ok_market, ok_events, ok_weekly, ok_5m, ok_1m, ok_closed_m, ok_closed_5, ok_closed_1, miss_ok, rerun_ok, fi_ok])

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
    "failure_injection_evidence": fi_ok,
    "wm_1m_present": wm_1m,
    "wm_5m_present": wm_5m,
  },
  "all_green": all_green,
  "decision": "READY_FOR_OCI_PHASE_B" if all_green else "PHASE_B_MATRIX_RESIDUAL",
}
(SHADOW/"MATRIX_SOAK_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
print(json.dumps({"all_green": all_green, "decision": summary["decision"], "checks": summary["checks"]}, ensure_ascii=False, indent=2))
PY

echo "MATRIX_SOAK_DONE $(date -Is)" | tee -a "$LOGDIR/matrix_soak_meta.log"
clear_locks
echo 0 > "$LOGDIR/matrix_soak.exit"
exit 0
