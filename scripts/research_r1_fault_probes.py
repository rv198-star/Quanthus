#!/usr/bin/env python3
"""Deterministic #612 negative cases; fake network only, synthetic market rows."""
import argparse
import copy
import importlib.machinery
import importlib.util
import json
import math
import sys
import tempfile
import types
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    loader = importlib.machinery.SourceFileLoader('candidate612', str(args.candidate))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    request = json.loads(args.request.read_text())
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wrong = copy.deepcopy(request)
        wrong['scope']['decision_end'] = '2025-12-30'
        wrong['authority']['revision'] = 'main'
        wrong['budget']['max_wall_seconds'] = -1
        m.write_json(root/'wrong.json', wrong)
        results['protected_scope_mutable_revision_negative_budget_accepted'] = m.load_request(root/'wrong.json') == wrong
        # Synthetic flat old listing, slight recent rise: threshold passes after 120 sessions.
        import duckdb
        con = duckdb.connect()
        venue = 'venue_bec59e31eb6bb28111a10d7e6d2c689f'
        con.execute(f"CREATE TABLE calendar AS SELECT 'S'||i AS calendar_session_id, '{venue}' AS venue_id, DATE '2020-01-02'+CAST(i AS INTEGER) AS trade_date, true AS is_open, CAST(NULL AS TIMESTAMP) AS knowledge_to FROM range(141) x(i)")
        con.execute("CREATE TABLE bar AS SELECT 'L' AS listing_id, calendar_session_id, trade_date AS observation_start, CASE WHEN row_number() OVER(ORDER BY trade_date)=122 THEN NULL ELSE 10.0 END AS open, CASE WHEN trade_date>=DATE '2020-05-01' THEN 10.3 ELSE 10.0 END AS close, 2e8::DOUBLE turnover, 2e7::DOUBLE volume, '1d' AS interval_size, 'UNADJUSTED' AS price_basis, CAST(NULL AS TIMESTAMP) AS knowledge_to FROM calendar")
        con.execute("CREATE TABLE state AS SELECT 'L' listing_id, calendar_session_id, trade_date observation_start, true is_tradable, true can_buy, true can_sell, 11.0 upper_price, 9.0 lower_price, CAST(NULL AS TIMESTAMP) knowledge_to FROM calendar")
        con.execute(f"CREATE TABLE listing AS SELECT 'L' listing_id, 'S' security_id, '000001' listing_code, '{venue}' venue_id, DATE '2010-01-01' effective_from, CAST(NULL AS DATE) effective_to, CAST(NULL AS TIMESTAMP) knowledge_to")
        con.execute("CREATE TABLE security AS SELECT 'S' security_id, 'common_equity' instrument_type, CAST(NULL AS TIMESTAMP) knowledge_to")
        locations = {'bar':'dwd_security_bar/interval_size=1d/year=2020','calendar':'dwd_trading_calendar/year=2020','state':'dwd_tradability_state/year=2020','listing':'dwd_listing/all','security':'dwd_security/all'}
        for table, location in locations.items():
            dest=root/'source/DWD/payload'/location/'part.parquet'; dest.parent.mkdir(parents=True)
            con.execute(f"COPY {table} TO '{dest}' (FORMAT PARQUET)")
        con.close()
        m.write_json(root/'source/DWD/manifest.json', {'deliberately':'wrong hash and wrong schema'})
        request['scope'].update({'read_start':'2020-01-02','decision_start':'2020-01-02','decision_end':'2020-05-20','execution_end':'2020-05-21'})
        m.write_json(root/'request.json', request)
        # The real screen accepts unverified input, and classifies a missing open as executable.
        m.command_screen(types.SimpleNamespace(request=root/'request.json', work=root))
        result=m.read_json(root/'result.json')
        results['screen_accepts_tampered_manifest_without_fetch_receipt'] = True
        results['synthetic_missing_open_universe_executable'] = result['totals']['universe_executable']
        results['synthetic_missing_open_expected_at_most'] = result['totals']['universe_rows']-1
        prices=[100*1.01**i for i in range(20)]
        mean=sum(prices)/20
        cv=math.sqrt(sum((x-mean)**2 for x in prices)/19)/mean*math.sqrt(252)
        results['constant_1pct_return_path']={'implemented_price_cv_annual':cv,'return_volatility_annual':0.0,'threshold':.5,'classification_disagrees':cv>.5}
        # Publish mismatch and retry use a memory-only fake hub. No network requests.
        m.write_json(root/'fetch-receipt.json', {'member_bytes':1,'request_id':'OTHER'})
        m.write_json(root/'run-receipt.json', {'wall_seconds':.01,'result_digest':'wrong'})
        result['totals']['nominal']=999999
        m.write_json(root/'result.json',result)  # preserve old result_digest deliberately
        uploads=[]
        class FakeAPI:
            def create_repo(self, **kw): return None
            def upload_folder(self, **kw): uploads.append(kw); return types.SimpleNamespace(oid='a'*40)
            def dataset_info(self, *a): return types.SimpleNamespace(sha='b'*40)
            def hf_hub_download(self, **kw): return str(root/'package'/request['publish']['path_prefix']/'manifest.json')
        sys.modules['huggingface_hub']=types.SimpleNamespace(HfApi=FakeAPI)
        one=m.command_publish(types.SimpleNamespace(request=root/'request.json',work=root))
        first=m.read_json(root/'publish-receipt.json')
        two=m.command_publish(types.SimpleNamespace(request=root/'request.json',work=root))
        second=m.read_json(root/'publish-receipt.json')
        results['publish_accepts_tampered_result_and_mismatched_receipts']=one==two==0
        results['publish_records_dataset_head_not_upload_commit']=first['revision']=='b'*40
        results['same_attempt_retry_changes_package_digest']=first['package_digest']!=second['package_digest']
        results['published_ready_present']=any(x.name=='READY' for x in (root/'package').rglob('*'))
        results['artifact_names']=list(m.read_json(root/'package-manifest.json')['artifacts'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))

if __name__=='__main__':
    main()
