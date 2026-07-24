# Hygiene fix — S1 RO perms + S3 zero-byte temp — 2026-07-25

Scope: **delivery readability / build hygiene only**.  
No data-calibre change: counts, checksums, manifest, gates unchanged.

## 1) S1 formal report RO export

### Root cause
`s1_report.json` was mode **600** (`root:root`) under
`data/revisions/s1_delisted_coverage_with_tb_20260724/`.  
`astock-ro` + `rrsync -ro` jail (`/srv/hermes-export/astock-data` bind,ro) could
traverse dirs but **not read** the file → OCI Permission denied.

Content SHA-256 (unchanged):
`10d977ff64a1a133af2307eb50dfd80fe8766606ed067ca118f2d5b6c8ba6066`

### Fix
- `chmod 644` on delivery files; dirs remain `755`
- Tool: `bin/publish_revision_ro_readable.py`
  - apply modes under `ASTOCK_HOME` only
  - preflight: `runuser -u astock-ro -- test -x/-r`
  - write-reject: create / append / delete denied
  - **rsync content probe** (not list-only) via forced key
- Note: env `ASTOCK_RO_USER=astock_ro` is the **Postgres** RO role; publisher maps
  that name to system account **`astock-ro`** (hyphen). Prefer
  leaving the system user as default.

### Evidence
```
preflight OK: astock-ro can traverse …/s1_delisted_coverage_with_tb_20260724 and read 1 file(s)
write-reject OK … (create/overwrite/delete denied)
rsync content probe OK: …/s1_report.json sha256=10d977ff… bytes=153365
```

Counts still:
- unexplained_missing_session_count = **0**
- invalid_observed_session_count = **0**
- terminal_boundary_session_count = **42**

### Process
Any new formal revision under `data/revisions/*` must run:
```bash
export ASTOCK_HOME=/root/work/astock/data
# system RO account is hyphenated; do not pass PG role name
python bin/publish_revision_ro_readable.py \
  "$ASTOCK_HOME/revisions/<rev>" \
  --rsync-probe revisions/<rev>/<file> \
  --expect-sha256 <sha>
```

## 2) S3 zero-byte temp cleanup

### Target (only)
`…/tradability_enriched_s2s3_v1_1_20260725/parts/.y2022.19936.parquet`

Confirmations before delete:
| check | result |
|-------|--------|
| size | **0** |
| in META.parts | **no** |
| manifest / consumer refs | **none** |

Deleted that path only. Formal `y2011`…`y2026` untouched.

### Builder hygiene
`bin/enrich_tradability_s2_s3_full_v1.py`:
- stage under `/tmp/astock_enrich_stage_*` (outside revision)
- atomic `os.replace` into `parts/yYYYY.parquet`
- cleanup staging on success/failure
- publish gate `_assert_parts_hygiene`: reject zero-byte, hidden, unregistered parquet

### Post-clean verification
| check | result |
|------:|--------|
| year parts | **16** |
| part row sum | **20,913,249** |
| main rows | **20,913,249** |
| main SHA-256 | `2b39d0bbe5c4e38fcb7051fc369fe0644241d580e7e7fec8b733a5304635da61` |
| manifest | unchanged |
| gate | **OPEN** |

## OCI
Please re-run RO pull of:
`revisions/s1_delisted_coverage_with_tb_20260724/s1_report.json`  
Expect success + same SHA. Write probes must still fail.
