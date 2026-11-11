#!/usr/bin/env python3
"""#612 bounded independent audit. Never changes or republishes the candidate."""
import argparse
import hashlib
import json
import math
import os
import resource
import shutil
import subprocess
import time
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--resume-verified-copy', action='store_true')
    args = parser.parse_args()
    start = time.monotonic()
    out = args.audit.resolve()
    out.mkdir(parents=True, exist_ok=args.resume_verified_copy)
    req = json.loads((Path(__file__).resolve().parents[1] / 'config/research/research-r1-audit/request.v1.json').read_text())
    manifest_path = args.original / 'source/DWD/manifest.json'
    assert sha(manifest_path) == req['source_manifest_sha256']
    manifest = json.loads(manifest_path.read_text())
    receipt = json.loads((args.original / 'fetch-receipt.json').read_text())
    original_request = json.loads((args.original / 'request.json').read_text())
    expected = {x['path']: x for x in manifest['content']['files'] if any(x['path'].startswith(p) for p in original_request['closure']['include_prefixes'])}
    assert len(expected) == 55
    assert {x['path'] for x in receipt['members']} == set(expected)
    copied = 0
    for name, entry in expected.items():
        assert '..' not in Path(name).parts
        if '/observation_start_year=' in name or '/trade_date_year=' in name:
            assert '/observation_start_year=2020/' in name or '/observation_start_year=2021/' in name or '/trade_date_year=2020/' in name or '/trade_date_year=2021/' in name
        src = args.original / 'source/DWD' / name
        dst = out / 'source/DWD' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert sha(src) == entry['sha256'].removeprefix('sha256:')
        if not args.resume_verified_copy:
            shutil.copyfile(src, dst)
        assert sha(dst) == entry['sha256'].removeprefix('sha256:')
        assert dst.stat().st_size == entry['size_bytes']
        copied += dst.stat().st_size
    assert copied == 187888538
    shutil.copyfile(manifest_path, out / 'source/DWD/manifest.json')
    originals = out / 'original-receipts'
    originals.mkdir(exist_ok=args.resume_verified_copy)
    for name in ['result.json', 'request.json', 'fetch-receipt.json', 'run-receipt.json', 'publish-receipt.json', 'package-manifest.json']:
        shutil.copyfile(args.original / name, originals / name)
    save(out / 'input-verification.json', {'manifest_sha256': sha(manifest_path), 'members': len(expected), 'bytes': copied, 'isolated_copy': True, 'copy_seconds': time.monotonic()-start})
    import duckdb
    import numpy as np
    import pandas as pd
    con = duckdb.connect()
    con.execute("SET threads=2")
    con.execute("SET memory_limit='3GB'")
    tables = {}
    for name in ['dwd_security_bar', 'dwd_trading_calendar', 'dwd_tradability_state', 'dwd_listing', 'dwd_security']:
        paths = [str(out / 'source/DWD' / p) for p in expected if p.startswith('payload/'+name+'/')]
        con.read_parquet(paths, hive_partitioning=True).create_view(name)
        tables[name] = con.execute(f'DESCRIBE {name}').fetchall()
    # Only row assembly uses SQL; feature windows and T+1 labels are independently computed in pandas.
    panel = con.execute("""
    SELECT l.listing_id, l.venue_id, CAST(l.effective_from AS DATE) listed,
      CAST(c.trade_date AS DATE) AS day, c.calendar_session_id,
      CAST(b.open AS DOUBLE) op, CAST(b.close AS DOUBLE) px,
      CAST(b.turnover AS DOUBLE) amount, CAST(t.upper_price AS DOUBLE) upper,
      t.is_tradable tradable, t.can_buy buy
    FROM dwd_listing l
    JOIN dwd_security s ON s.security_id=l.security_id AND s.knowledge_to IS NULL
    JOIN dwd_trading_calendar c ON c.venue_id=l.venue_id AND c.knowledge_to IS NULL AND c.is_open
    LEFT JOIN dwd_security_bar b ON b.listing_id=l.listing_id AND b.calendar_session_id=c.calendar_session_id
      AND b.knowledge_to IS NULL AND b.interval_size='1d' AND b.price_basis='UNADJUSTED'
    LEFT JOIN dwd_tradability_state t ON t.listing_id=l.listing_id AND t.calendar_session_id=c.calendar_session_id
      AND t.knowledge_to IS NULL
    WHERE l.knowledge_to IS NULL AND s.instrument_type IN ('common_equity','equity')
      AND l.venue_id IN ('venue_bec59e31eb6bb28111a10d7e6d2c689f','venue_8c0bd9088a0dc88f28e5e7fa8efdce87')
      AND CAST(c.trade_date AS DATE) BETWEEN DATE '2020-01-02' AND DATE '2021-12-31'
      AND CAST(c.trade_date AS DATE)>=CAST(l.effective_from AS DATE)
      AND (l.effective_to IS NULL OR CAST(c.trade_date AS DATE)<=CAST(l.effective_to AS DATE))
    ORDER BY l.listing_id, day
    """).df()
    con.close()
    assert not panel.duplicated(['listing_id','day']).any()
    parts = []
    for _, g in panel.groupby('listing_id', sort=False):
        g = g.copy()
        close = g.px
        prior = close.shift(1)
        rolling = prior.rolling(20, min_periods=20)
        vol = rolling.std(ddof=1) / rolling.mean() * np.sqrt(252)
        amount = g.amount.shift(1).rolling(20, min_periods=1).mean()
        nominal_other = (rolling.count()==20) & (amount>=1e8) & (vol<=.5) & ((close/prior-1).abs()<=.04) & (close/close.shift(20)-1).between(.02,.12)
        nominal = nominal_other & (np.arange(len(g))+1>=120)
        universe = close.notna() & g.tradable.fillna(False)
        limit = g.op.notna() & g.upper.notna() & (g.op>=g.upper-.005)
        executable = close.notna() & g.tradable.fillna(False) & g.buy.fillna(False) & ~limit
        strict_exec = executable & g.op.notna() & (g.op>0)
        r = pd.DataFrame({'day':g.day, 'listing':g.listing_id, 'venue':g.venue_id,
          'universe':universe, 'nominal':universe & nominal,
          'executable':executable.shift(-1,fill_value=False),
          'strict_exec':strict_exec.shift(-1,fill_value=False),
          'missing':close.isna().shift(-1,fill_value=False),
          'suspended':(~g.tradable.fillna(False)).shift(-1,fill_value=False),
          'cannot_buy':(g.tradable.fillna(False)&~g.buy.fillna(False)).shift(-1,fill_value=False),
          'limit':limit.shift(-1,fill_value=False),
          'age_truncated':universe & nominal_other & (np.arange(len(g))+1<120) & (g.listed<pd.Timestamp('2019-01-01'))})
        parts.append(r[r.day<=pd.Timestamp('2021-12-30')])
    rows = pd.concat(parts,ignore_index=True)
    def summarize(r):
        n = r.nominal
        u = r.universe
        return {'sessions':len(r),'trading_days':int(r.day.nunique()), 'universe_rows':int(u.sum()),'universe_executable':int((u&r.executable).sum()),'nominal':int(n.sum()),'nominal_executable':int((n&r.executable).sum()),'nominal_loss_rate':round(1-int((n&r.executable).sum())/int(n.sum()),6) if n.sum() else None, 'universe_loss_rate':round(1-int((u&r.executable).sum())/int(u.sum()),6) if u.sum() else None, 'nominal_blocked_reasons':{f'next_{k if k!="missing" else "missing_bar"}':int((n&r[k]).sum()) for k in ['missing','suspended','cannot_buy','limit']}}
    total = summarize(rows)
    total['nominal_blocked_reasons']['next_limit_blocked'] = total['nominal_blocked_reasons'].pop('next_limit')
    old = json.loads((args.original/'result.json').read_text())
    diff = {k:{'original':v,'independent':total.get(k)} for k,v in old['totals'].items() if total.get(k)!=v}
    report = {'audit_git':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(), 'candidate_git':req['candidate_git'], 'totals':total,'differences':diff, 'by_year':{str(k):summarize(g) for k,g in rows.groupby(rows.day.dt.year)},'by_venue':{str(k):summarize(g) for k,g in rows.groupby('venue')}, 'diagnostics':{'first_nominal_date':rows.loc[rows.nominal,'day'].min(), 'last_nominal_date':rows.loc[rows.nominal,'day'].max(), 'old_listings_excluded_by_window_age_only':int(rows.age_truncated.sum()), 'null_or_nonpositive_open_executable_universe':int((rows.universe&rows.executable&~rows.strict_exec).sum()),'unknown_tradability_panel_rows':int(panel.tradable.isna().sum()),'can_buy_differs_from_tradable':int((panel.buy.fillna(False)!=panel.tradable.fillna(False)).sum()), 'distinct_nominal_listings':int(rows.loc[rows.nominal,'listing'].nunique()), 'top10_listing_nominal_share':float(rows.loc[rows.nominal].groupby('listing').size().nlargest(10).sum()/rows.nominal.sum())}, 'runtime':{'python':os.sys.version,'duckdb':duckdb.__version__,'pandas':pd.__version__,'numpy':np.__version__,'wall_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, 'claim':'Reproduces original implemented proxy; diagnostic only, no corrected strategy or unseen sample'}
    save(out/'independent-reproduction.json',report)
    print(json.dumps(report,ensure_ascii=False,default=str))
    if diff:
        raise SystemExit(2)

if __name__=='__main__':
    main()
