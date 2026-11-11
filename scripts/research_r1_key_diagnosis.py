#!/usr/bin/env python3
"""Locate duplicated consumer keys in the already verified #612 input closure."""
import argparse
import json
from pathlib import Path
import duckdb
p=argparse.ArgumentParser();p.add_argument('--audit',type=Path,required=True);a=p.parse_args()
c=duckdb.connect();c.execute('SET threads=2');c.execute("SET memory_limit='2GB'")
report={}
for table,key in [('dwd_security','security_id'),('dwd_listing','listing_id'),('dwd_trading_calendar','calendar_session_id')]:
 paths=sorted(str(x) for x in (a.audit/'source/DWD/payload'/table).rglob('*.parquet'))
 c.read_parquet(paths,hive_partitioning=True).create_view(table)
 columns=[x[0] for x in c.execute('DESCRIBE '+table).fetchall()]
 duplicates=c.execute(f'SELECT {key}, count(*) n FROM {table} WHERE knowledge_to IS NULL GROUP BY 1 HAVING count(*)>1 ORDER BY 1').fetchall()
 report[table]={'columns':columns,'duplicate_current_keys':duplicates}
 if duplicates:
  rows=c.execute(f'SELECT * FROM {table} WHERE knowledge_to IS NULL AND {key} IN (SELECT {key} FROM {table} WHERE knowledge_to IS NULL GROUP BY 1 HAVING count(*)>1) ORDER BY {key} LIMIT 20').fetchall()
  report[table]['duplicate_rows']=[dict(zip(columns,row)) for row in rows]
c.read_parquet(str(a.audit/'independent-panel.parquet')).create_view('panel')
report['panel_duplicate_listings']=c.execute('SELECT listing_id,count(*) rows,count(DISTINCT day) dates,min(day),max(day) FROM panel GROUP BY 1 HAVING count(*)>count(DISTINCT day) ORDER BY 1').fetchall()
(a.audit/'key-diagnosis.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
print(json.dumps(report,default=str))
