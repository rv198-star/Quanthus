"""Standalone replay web server (issue #748, phase-1 experience update).

A dedicated, isolated instance - its own state dir, its own account, its
own port.  It never mounts the 8791 product-web bundle and never touches
the live store.  The page is a self-contained Chinese console: 历史回放·
模拟 badge + 当前回放日期 always on top, per-day 建议 (大白话理由),
confirm/skip, 下一交易日 carry-in, 每日回顾, one-click reset, and
「自动执行到指定日期」 driving the SAME engine the manual steps use.

Routes (JSON except GET /):
  GET  /                replay console
  GET  /api/state       replay meta + workbench + open plans + account + CN fields
  GET  /api/review      the current day's 每日回顾
  GET  /api/log         engine event log
  POST /api/run-day     materialize input + run_daily for the current day
  POST /api/confirm     {plan_id}
  POST /api/skip        {plan_id}
  POST /api/advance     expire resting orders -> next session 09:25
  POST /api/auto        {target?} - confirm everything and advance to the day
  POST /api/reset       wipe replay state (explicit one-click reset)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from quanthus.p12_replay.engine import ReplayEngine

LOGIN_PAGE = r"""<!doctype html>
<html lang=zh><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>P12 历史回放 · 登录</title>
<style>body{margin:0;font:14px -apple-system,"PingFang SC",sans-serif;background:#f6f7f8;display:flex;align-items:center;justify-content:center;min-height:100vh}.box{background:#fff;border:1px solid #e3e5e8;border-radius:8px;padding:36px 40px;width:340px}.badge{font-weight:700;color:#fff;background:#8c1d18;padding:4px 14px;border-radius:3px;font-size:13px;display:inline-block;margin-bottom:18px}h1{font-size:19px;margin:0 0 6px}p{color:#7a7f85;font-size:13px;margin:0 0 20px}input{width:100%;padding:9px 12px;border:1px solid #e3e5e8;border-radius:4px;margin-bottom:12px;font:inherit;box-sizing:border-box}button{width:100%;padding:10px;background:#1a1d21;color:#fff;border:none;border-radius:4px;font:inherit;cursor:pointer}#err{color:#a02215;font-size:13px;min-height:18px;margin-top:8px}</style></head>
<body><div class=box><span class=badge>历史回放 · 模拟</span><h1>登录</h1><p>这是独立回放控制台，与 8791/8790 完全隔离。</p><form onsubmit="return go()"><input id=u placeholder=用户名 autocomplete=username><input id=pw type=password placeholder=密码 autocomplete=current-password><button>进入</button></form><div id=err></div>
<script>async function go(){const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u.value,password:pw.value}),credentials:'same-origin'});const d=await r.json();if(r.ok&&d.ok){location='/'}else{err.textContent=d.error||'用户名或密码不对'}return false}</script>
</body></html>
"""

PAGE = r"""<!doctype html>
<html lang=zh><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>P12 历史回放 · 模拟</title>
<style>
:root{--ink:#1a1d21;--muted:#7a7f85;--line:#e3e5e8;--bg:#f6f7f8;--accent:#8c1d18;--ok:#1d6f42;--warn:#9a6200;--info:#2b4f81}
*{box-sizing:border-box}body{margin:0;font:14px/1.65 -apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--bg);color:var(--ink)}
header{background:#fff;border-bottom:1px solid var(--line);padding:12px 28px;display:flex;align-items:baseline;gap:16px;position:sticky;top:0;z-index:9;flex-wrap:wrap}
.badge{font-weight:700;letter-spacing:.12em;color:#fff;background:var(--accent);padding:4px 14px;border-radius:3px;font-size:13px;white-space:nowrap}
.day{font-size:20px;font-weight:600}.meta{color:var(--muted);font-size:12px;margin-left:auto;text-align:right}
main{max-width:1120px;margin:22px auto;padding:0 20px;display:grid;grid-template-columns:1fr 350px;gap:20px}
.card{background:#fff;border:1px solid var(--line);border-radius:6px;padding:18px 20px;margin-bottom:16px}
h2{font-size:13px;letter-spacing:.14em;color:var(--muted);margin:0 0 12px;font-weight:600}
.plan{border:1px solid var(--line);border-radius:5px;padding:14px 16px;margin-bottom:10px}
.plan .code{font-size:17px;font-weight:600}.plan .name{color:var(--muted);font-size:13px}
.side{display:inline-block;font-size:12px;font-weight:700;padding:1px 10px;border-radius:2px;margin-left:8px;vertical-align:2px}
.buy{background:#fdecea;color:#a02215}.sell{background:#e9f3ec;color:var(--ok)}.hold{background:#eef1f4;color:var(--info)}.skip{background:#f3ede0;color:var(--warn)}
.kv{display:flex;gap:22px;margin:8px 0;font-variant-numeric:tabular-nums;flex-wrap:wrap}
.kv b{font-weight:600}.kv span{color:var(--muted);font-size:12px;display:block}
.reasons{margin:8px 0 0;padding-left:18px;font-size:13px;color:var(--ink)}
.reasons li{margin:2px 0}.reasons.cond{color:var(--muted);font-size:12px}
.status{font-size:12px;padding:5px 12px;border-radius:3px;display:inline-block}
.st-PUBLISHED,.st-READY,.st-filled{background:#e9f3ec;color:var(--ok)}
.st-INPUT_BLOCKED,.st-FAILED,.st-rejected{background:#fdecea;color:#a02215}
.st-PRECHECK,.st-SKIPPED,.st-not_marketable{background:#f3ede0;color:var(--warn)}
.st-ALREADY_PUBLISHED{background:#e8eef7;color:var(--info)}
button{font:inherit;padding:8px 20px;border-radius:4px;border:1px solid var(--line);background:#fff;cursor:pointer}
button.primary{background:var(--ink);color:#fff;border-color:var(--ink)}
button.warn{color:var(--accent);border-color:var(--accent)}
button.next{background:var(--accent);color:#fff;border-color:var(--accent);padding:12px 30px;font-size:15px;font-weight:600;width:100%}
button:disabled{opacity:.35;cursor:default}
.actions{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.empty{color:var(--muted);font-style:italic}
footer{max-width:1120px;margin:0 auto 40px;padding:0 20px;color:var(--muted);font-size:12px;line-height:1.8}
.log{max-height:160px;overflow:auto;font:11px/1.5 ui-monospace,monospace;background:#fbfbfa;border:1px solid var(--line);border-radius:4px;padding:10px;white-space:pre-wrap}
#flash{position:fixed;top:70px;right:24px;background:#1a1d21;color:#fff;padding:10px 18px;border-radius:4px;font-size:13px;opacity:0;transition:opacity .25s;pointer-events:none;z-index:99;max-width:420px}
.acct td{padding:3px 14px 3px 0;font-variant-numeric:tabular-nums}
.auto{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap}
.auto input{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:4px;width:150px}
.note{font-size:12px;color:var(--muted);margin-top:6px}
.disc{font-size:12px;color:var(--warn);background:#fdf8ec;border:1px solid #f0e2b8;border-radius:4px;padding:8px 12px;margin-top:10px}
.review{border-top:1px dashed var(--line);padding-top:12px;margin-top:12px;font-size:13px}
.review h3{font-size:13px;margin:0 0 8px;color:var(--ink)}
.review table{width:100%;border-collapse:collapse;font-size:12px}
.review td,.review th{padding:4px 8px 4px 0;border-bottom:1px solid #f0f0ee;text-align:left;vertical-align:top}
.review th{color:var(--muted);font-weight:600;white-space:nowrap}
.chip{display:inline-block;background:#eef1f4;border-radius:3px;padding:1px 8px;font-size:11px;color:var(--info);margin-right:6px}
.covnote{font-size:12px;color:var(--muted);margin:0 0 10px;line-height:1.5}
.covnote b{color:var(--ink)}
.covrow{display:flex;align-items:baseline;gap:8px;padding:5px 0;border-bottom:1px solid #f0f0ee;font-size:12px;flex-wrap:wrap}
.covrow:last-child{border-bottom:none}
.covrow .c{font-weight:600;min-width:64px}
.covtag{font-size:11px;padding:1px 8px;border-radius:2px;white-space:nowrap}
.cov-eval{background:#e9f3ec;color:var(--ok)}
.cov-pend{background:#f3ede0;color:var(--warn)}
.cov-na{background:#eef1f4;color:var(--muted)}
.cov-block{background:#f6e8e8;color:#b03a3a}
.covrow .v{color:var(--info);font-weight:600}
.covrow .r{color:var(--muted);flex:1;min-width:160px}.covdetail{margin:2px 0 4px 0;padding-left:4px;font-size:12px;line-height:1.7;color:var(--muted)}.covdetail .chk{display:inline-block;margin-right:14px}.covdetail .chk.pass{color:#1a7f37}.covdetail .chk.fail{color:#b03a3a}.covdetail .gates{color:#6a737d;margin-right:14px}
tabs{display:flex;gap:0;margin-bottom:0}
.tab{padding:8px 18px;cursor:pointer;border:1px solid var(--line);border-bottom:none;border-radius:5px 5px 0 0;background:#f3f4f5;font-size:13px}
.tab.on{background:#fff;font-weight:600;border-bottom:1px solid #fff;margin-bottom:-1px}
.tabbody{border:1px solid var(--line);border-radius:0 5px 5px 5px;background:#fff;padding:16px 18px}
</style></head><body>
<header>
 <span class=badge>历史回放 · 模拟</span>
 <span class=day id=day>—</span>
 <span class=meta id=meta></span>
</header>
<div id=flash></div>
<main>
<section>
 <div class=card>
  <tabs><div class="tab on" id=tab-today onclick=showTab('today')>今日操作</div><div class=tab id=tab-review onclick=showTab('review')>每日回顾</div></tabs>
  <div class=tabbody id=body-today>
   <h2>今日建议</h2><div id=plans><p class=empty>尚未生成本日建议。点「运行当晚任务」生成。</p></div>
   <div class=actions data-write><button class=primary id=runbtn>运行当晚任务</button><span id=runstatus></span></div>
   <div id=reviewbanner style="display:none;margin:8px 0;padding:8px 10px;border:1px solid var(--warn,#a55);border-radius:6px;color:var(--warn,#a55);font-size:13px"></div>
  <div id=coverage></div>
   <h2 style="margin-top:18px">记录</h2><div id=orders class=empty>—</div>
   <h2 style="margin-top:14px">引擎日志</h2><div class=log id=log>—</div>
  </div>
  <div class=tabbody id=body-review style="display:none">
   <div id=review><p class=empty>完成一天的操作后，这里汇总当日建议、你的处理、成交与账户变化。</p></div>
  </div>
 </div>
</section>
<aside>
 <div class=card><h2>账户</h2>
  <table class=acct>
   <tr><td>现金</td><td id=cash>—</td></tr>
   <tr><td>市值</td><td id=mkt>—</td></tr>
   <tr><td>持仓</td><td id=pos>—</td></tr>
   <tr><td>订单</td><td id=ord>—</td></tr>
   <tr><td>对账</td><td id=recon>—</td></tr>
  </table>
  <div class=disc>历史回放 · 模拟，不代表策略评估</div>
 </div>
 <div class=card><h2>推进</h2>
  <p class=note>结束当前交易时段：持仓与资金进入下一交易日，未成交委托按当日规则失效。</p>
  <button class=next id=adv>下一交易日 →</button>
  <div class=auto>
   <input type=date id=auto-date>
   <button id=autobtn>自动执行到此日</button>
  </div>
  <p class=note>自动执行：逐日确认全部建议并推进，随时可停在一天再手动操作。</p>
  <div style="margin-top:12px"><button id=reset>重置回放</button></div>
 </div>
 <div class=card><h2>包 / 回执</h2><div id=pkg style="font:11px/1.6 ui-monospace,monospace;word-break:break-all">—</div></div>
</aside>
</main>
<footer>独立回放实例 · 与 8791/8790 完全隔离 · <span id=rulesrc>合成规则驱动</span> · 成交按真实日线开盘价判定 · 回放证据在引擎日志与记录页可核</footer>
<script>
let S=null;
async function api(p,b){const t=sessionStorage.getItem('replay-token');const m=b===undefined?'GET':'POST';const r=await fetch(p,{method:m,headers:{'Content-Type':'application/json','Authorization':'Bearer '+(t||'')},body:b===undefined?undefined:JSON.stringify(b)});if(r.status===401){sessionStorage.removeItem('replay-token');location.reload();return{}}if(!r.ok){let e={status:'http_'+r.status};try{e=await r.json()}catch(_){e.error='HTTP '+r.status}return e}return r.json()}
function fmt(x){return x==null?'—':x}
function flash(t){const f=document.getElementById('flash');f.textContent=t;f.style.opacity=1;setTimeout(()=>f.style.opacity=0,2600)}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function lines(arr,cls){if(!arr||!arr.length)return'';return '<ul class="reasons '+(cls||'')+'">'+arr.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>'}
function showTab(k){document.getElementById('tab-today').className='tab'+(k==='today'?' on':'');document.getElementById('tab-review').className='tab'+(k==='review'?' on':'');document.getElementById('body-today').style.display=k==='today'?'':'none';document.getElementById('body-review').style.display=k==='review'?'':'none';if(k==='review')loadReview()}

function sideClass(label){if(label==='买入建议')return'buy';if(label==='卖出建议'||label==='退出建议'||label==='减仓建议')return'sell';if(label==='继续持有')return'hold';if(label==='已跳过')return'skip';return'hold'}

function renderCoverage(){
 const el=document.getElementById('coverage');const c=(S&&S.coverage)||null;
 if(!c){el.innerHTML='';return}
 let h='<p class=covnote><b>'+esc(c.note_text||'')+'</b>'+(c.replay_message?'<span style="font-weight:400;color:var(--muted)"> · '+esc(c.replay_message)+'</span>':'')+'</p>';
 const _all=c.securities||[];const rows=_all.slice().sort((a,b)=>{const ea=a.evaluated?1:0,eb=b.evaluated?1:0;if(ea!==eb)return eb-ea;const ha=a.scan_detail?1:0,hb=b.scan_detail?1:0;if(ha!==hb)return hb-ha;return String(a.security_code).localeCompare(String(b.security_code))});
 if(rows.length){h+='<div style="margin-top:4px">';
  for(const r of rows){
   const evd=!!r.evaluated;const st=r.state||'';const tag=evd?'<span class="covtag cov-eval">已评估</span>':(st==='blocked'?'<span class="covtag cov-block">时钟阻断</span>':'<span class="covtag cov-pend">待核</span>');const verdict=evd&&r.verdict?'<span class=v>'+esc(r.verdict)+'</span>':'';const srl=evd&&r.scan_reason?esc(r.scan_reason):(evd?'':esc(r.scan_reason||'未评估'));
    h+='<div class=covrow><span class=c>'+esc(r.security_code)+'</span>'+tag+verdict+(srl?'<span class=r>'+srl+'</span>':'')+'</div>';   const sd=r.scan_detail||null;   if(sd){    let dh='<div class=covdetail>';    if((sd.gates
  }
  h+='</div>'
 }
 el.innerHTML=h
 const rn=c.runner||{};
 const src=(rn.ref_id?(rn.ref_id+(rn.version?('@'+rn.version):'')):'')||'合成规则驱动';
 const rse=document.getElementById('rulesrc');if(rse)rse.textContent='规则来源 '+src
}
async function refresh(){
 S=await api('/api/state');const r=S.replay||{};const rb=document.getElementById('reviewbanner');if(rb){rb.style.display=S.review_notice?'':'none';rb.textContent=S.review_notice||''}
 document.getElementById('day').textContent=(r.current_day||'—')+' · 第 '+(r.day_index+1)+'/'+r.sessions_total+' 个交易日';
 const ev=S.readonly_evidence||{};
 if(S.read_only){
  document.querySelectorAll('[data-write]').forEach(x=>x.style.display='none');
  const an=document.getElementById('advancenote');if(an)an.style.display='none';
  const au=document.getElementById('autonote');if(au)au.style.display='none';
  document.getElementById('meta').innerHTML='<span style="color:var(--warn,#a55);font-weight:600">只读预览 · 未正式采用</span><br>逻辑决定时点 '+fmt(ev.logical_decision_at);
 }else{
  document.querySelectorAll('[data-write]').forEach(x=>x.style.display='');
  const an=document.getElementById('advancenote');if(an)an.style.display='';
  const au=document.getElementById('autonote');if(au)au.style.display='';
  document.getElementById('meta').innerHTML='回放时刻 '+fmt(r.now)+'<br>下一交易日 '+fmt(r.next_day);
 }
 const wb=S.workbench||{};
 const ops=S.opportunities_cn||[];
 let ph='';
 if(!ops.length){ph='<p class=empty>本日无可执行建议'+(wb.run_receipt?('（回执 '+wb.run_receipt.status+'）'):'')+'</p>'}
 for(const it of ops){
  const st=(it.status||'').toUpperCase();
  ph+='<div class=plan><span class=code>'+esc(it.security_code)+' <span class=name>'+esc(it.security_name||'')+'</span></span>'
   +'<span class="side '+sideClass(it.action_label)+'">'+esc(it.action_label||'提示')+'</span>'
   +(st?'<span class="status st-'+st+'" style="margin-left:6px">'+st+'</span>':'')
   +'<div class=kv>'
   +(it.delta_qty?'<div><span>数量</span><b>'+fmt(it.delta_qty)+'</b></div>':'')
   +(it.price_cap?'<div><span>限价</span><b>'+fmt(it.price_cap)+'</b></div>':'')
   +(it.eligible_at?'<div><span>窗口</span><b style="font-size:11px">'+fmt(it.eligible_at)+' → '+fmt(it.expires_at)+'</b></div>':'')
   +'</div>'
   +lines(it.reason_lines)
   +lines(it.condition_lines,'cond')
   +(it.constraint?'<div class=note>约束：'+esc(it.constraint)+'</div>':'')
   +((!S.read_only)&&it.plan_id&&st!=='SKIPPED'&&st!=='ENDED'&&st!=='EXPIRED'?'<div class=actions><button class=primary onclick=act("confirm","'+it.plan_id+'")>确认</button><button class=warn onclick=act("skip","'+it.plan_id+'")>跳过</button></div>':'')
   +'</div>';
 }
 document.getElementById('plans').innerHTML=ph;
 renderCoverage();
 const a=S.account||{};const acct=(wb.account||{});
 document.getElementById('cash').textContent=fmt(a.cash);
 const mkt=acct.market_value||acct.total_value||null;
 document.getElementById('mkt').textContent=mkt?fmt(mkt):'—';
 document.getElementById('pos').textContent=(a.positions||[]).map(p=>p.security_code+'×'+p.quantity).join('，')||'无';
 document.getElementById('ord').textContent=fmt(a.orders);document.getElementById('recon').textContent=a.rebuild_matches?'通过':'—';
 const ords=(wb.orders||[]);document.getElementById('orders').innerHTML=ords.length?ords.map(o=>esc(o.order_id)+' · '+esc(o.side)+' '+esc(o.security_code)+' ×'+esc(o.accepted_total_qty)+' · '+esc(o.status)).join('<br>'):'—';
 const pkg=S.package||{};const runs=S.runs||[];
  if(S.read_only){
   document.getElementById('pkg').textContent='原 P11 包 '+(ev.package_id||'—')+' · '+String(ev.package_digest||'').slice(0,26)+'… · 策略 '+(ev.policy_id||'—')+' '+(ev.policy_version||'')+' · 决定时点 '+fmt(ev.decision_cutoff||ev.logical_decision_at)+' · 只读预览';
  }else{
   document.getElementById('pkg').textContent=(pkg.package_id?('pkg '+pkg.package_id+' · '+String(pkg.file_sha256||'').slice(0,19)+'… · '+fmt(pkg.published_at)+(pkg.retracted?' [已撤回]':'')):'无包')+(runs.length?'\n回执：'+runs.map(x=>x.market_date+':'+x.status).join('  '):'');
  }
 document.getElementById('adv').disabled=(S.replay&&!S.replay.next_day);
 if(document.getElementById('body-review').style.display!=='none')loadReview();
}
async function act(k,id){const r=await api('/api/'+k,{plan_id:id});flash((k==='confirm'?'确认 ':'跳过 ')+id+' → '+(r.status||r.plan_id||'ok'));refresh()}
async function loadReview(){const R=await api('/api/review');const adv=R.advice||[];let h='<h3>'+(R.market_date||'')+' 当日回顾</h3>';if(!adv.length&&!((R.fills_today||[]).length)){h+='<p class=empty>当日无建议或尚未运行当晚任务。</p>'}else{h+='<table><tr><th>建议</th><th>理由</th><th>处理</th><th>成交</th></tr>';for(const a of adv){const fl=(a.fills||[]).map(f=>esc(f.side)+' '+esc(f.security_code)+' ×'+esc(f.quantity)+' @'+esc(f.price)).join('<br>')||'—';h+='<tr><td><b>'+esc(a.security_code)+'</b> '+esc(a.action_label)+'</td><td>'+((a.reason_lines||[]).map(esc).join('；')||'—')+'</td><td>'+esc(a.owner_action)+(a.skip_receipt&&a.skip_receipt.skipped_at?'<div class=note style=\"margin-top:2px\">跳过时间 '+esc(String(a.skip_receipt.skipped_at).replace('T',' ').slice(0,16))+'</div>':'')+'</td><td>'+fl+'</td></tr>'}h+='</table>';const acc=R.account||{};h+='<div class=note style="margin-top:8px">现金 '+fmt(acc.cash)+' · 持仓 '+((acc.positions||[]).map(p=>esc(p.security_code)+'×'+esc(p.quantity)).join('，')||'无')+' · 对账 '+(acc.rebuild_matches?'通过':'—')+'</div>'}
h+='<div class=disc>'+esc(R.note||'')+'</div>';document.getElementById('review').innerHTML=h}
document.getElementById('runbtn').onclick=async()=>{const r=await api('/api/run-day',{});flash('运行 '+((r.status)||'完成'));refresh()};
document.getElementById('adv').onclick=async()=>{const r=await api('/api/advance',{});flash(r.finished?'回放结束':'进入 '+(r.market_date||''));refresh()};
document.getElementById('autobtn').onclick=async()=>{const d=document.getElementById('auto-date').value;if(!d){flash('先选目标日期');return}flash('自动执行到 '+d+' …');const r=await api('/api/auto',{target:d});flash(r.error?('失败：'+r.error):(r.status&&r.status!=='filled'&&r.stopped_at===undefined?('返回 '+r.status):'已推进 '+(r.processed_days!=null?r.processed_days:0)+' 天，停在 '+(r.stopped_at||'')));refresh()};
document.getElementById('reset').onclick=async()=>{if(confirm('清空回放状态并回到第一个交易日？')){await api('/api/reset',{});flash('已重置');location.reload()}};
async function loadLog(){const t=await fetch('/api/log').then(r=>r.text());document.getElementById('log').textContent=t||'—'}
refresh();loadLog();setInterval(()=>{refresh();loadLog()},5000);
</script></body></html>
"""


import os as _os

_READ_ONLY = _os.environ.get("P12_REPLAY_READONLY") in ("1", "true", "yes")


def _read_body(handler) -> dict:
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        n = 0
    if n <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(n) or b"{}")
    except json.JSONDecodeError:
        return {}


def make_handler(engine_factory, users: dict | None = None):
    # users: {username: (role, password)} - same shape as the P12
    # local_server's --user seeding.  Sessions are in-memory bearer
    # tokens; a restart ends every session (the replay is a lab console,
    # not a durable account).  When users is empty the console stays
    # open (local loopback only).
    users = users or {}
    sessions: dict[str, tuple[str, float]] = {}
    SESSION_TTL = 12 * 3600

    def _check_auth(headers) -> str | None:
        if not users:
            return "local"
        auth = headers.get("Authorization") or ""
        token = None
        if auth.lower().startswith("bearer "):
            token = auth.split(None, 1)[1].strip()
        else:
            cookie = headers.get("Cookie") or ""
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("replay_token="):
                    token = part.split("=", 1)[1].strip()
                    break
        if token is None:
            return None
        rec = sessions.get(token)
        if rec is None or rec[1] < time.time():
            return None
        return rec[0]

    class ReplayHandler(BaseHTTPRequestHandler):
        server_version = "p12-replay/2"

        def _engine(self) -> ReplayEngine:
            return self.server.engine

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _text(self, payload: str, status: int = 200, ctype="text/plain; charset=utf-8") -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                if users and _check_auth(self.headers) is None:
                    return self._text(LOGIN_PAGE, ctype="text/html; charset=utf-8")
                return self._text(PAGE, ctype="text/html; charset=utf-8")
            if path == "/health":
                return self._json({"ok": True, "now": self._engine().now().isoformat()})
            if users and _check_auth(self.headers) is None:
                return self._json({"error": "unauthorized"}, 401)
            if path == "/api/state":
                payload = self._engine().day_state()
                if _READ_ONLY:
                    payload = dict(payload)
                    payload["read_only"] = True
                    payload["review_notice"] = (
                        "读数据模块复审中，未正式采用；确认与运行入口已关闭。"
                    )
                return self._json(payload)
            if path == "/api/review":
                payload = self._engine().day_review()
                if _READ_ONLY:
                    payload = dict(payload)
                    payload["read_only"] = True
                    payload["review_notice"] = (
                        "读数据模块复审中，未正式采用；确认与运行入口已关闭。"
                    )
                return self._json(payload)
            if path == "/api/log":
                log = self._engine().state_dir / "replay-log.jsonl"
                text = log.read_text(encoding="utf-8") if log.is_file() else ""
                return self._text(text)
            return self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            body = _read_body(self)
            # Read-only review mode (P12_REPLAY_READONLY=1, coordinator-approved
            # fallback for the in-review #749 input): the console still serves
            # login + GET, but every mutating POST is refused at the server
            # with an explicit locked receipt - never a silent no-op.
            if _READ_ONLY and path != "/api/login":
                return self._json(
                    {
                        "error": "read_only",
                        "status": "REVIEW_LOCKED",
                        "detail": "读数据模块复审中，未正式采用；所有确认/运行/推进入口在服务端关闭。",
                    },
                    403,
                )
            if path == "/api/login":
                username = str(body.get("username") or "")
                password = str(body.get("password") or "")
                rec = users.get(username)
                if rec is not None and hmac.compare_digest(
                    rec[1].encode(), password.encode()
                ):
                    token = secrets.token_urlsafe(32)
                    sessions[token] = (username, time.time() + SESSION_TTL)
                    body = json.dumps({"ok": True, "token": token, "user": username, "role": rec[0]}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Set-Cookie", f"replay_token={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return self._json({"error": "用户名或密码不对"}, 401)
            if users and _check_auth(self.headers) is None:
                return self._json({"error": "unauthorized"}, 401)
            engine = self._engine()
            try:
                if path == "/api/run-day":
                    return self._json(engine.evening_run())
                if path == "/api/confirm":
                    return self._json(engine.confirm(str(body.get("plan_id") or "")))
                if path == "/api/skip":
                    return self._json(engine.skip(str(body.get("plan_id") or "")))
                if path == "/api/advance":
                    return self._json(engine.advance_day())
                if path == "/api/auto":
                    target = body.get("target")
                    target_date = date.fromisoformat(str(target)) if target else None
                    return self._json(engine.auto_advance(target=target_date))
                if path == "/api/reset":
                    self.server.engine = engine_factory(reset=True)
                    return self._json({"ok": True, "reset_at": datetime.now().isoformat()})
            except Exception as exc:  # noqa: BLE001 - honest error, never a fake ok
                return self._json({"error": type(exc).__name__, "detail": str(exc)}, 500)
            return self._json({"error": "not found"}, 404)

    return ReplayHandler


def serve(engine_factory, host: str, port: int, users: dict | None = None) -> ThreadingHTTPServer:
    handler = make_handler(engine_factory, users)
    server = ThreadingHTTPServer((host, port), handler)
    server.engine = engine_factory(reset=False)
    return server
