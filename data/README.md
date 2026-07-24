# Local Data Directory

This directory is intentionally excluded from Git except for this file. Planned local layout:

```text
data/
  landing/<source>/<acquired-date>/
  normalized/<dataset>/<market-date>/
  curated/<dataset>/<market-date>/
  snapshots/<snapshot-id>/
  manifests/
```

Never place credentials in data manifests. Large reproducibility artifacts belong in the
approved artifact store and are referenced by checksum and immutable id.

`quanthus.data_foundation.SnapshotCatalog` publishes each snapshot as an atomic directory
containing `snapshot.json`. The catalog metadata is local runtime state and remains excluded
from Git along with the market data it describes.

Run `make data-audit` after `make bootstrap-p1` to audit the stable Hermes snapshot at
`/home/ubuntu/data/astock`. Set `QUANTHUS_DATA_ROOT` to use another local snapshot. The audit
is read-only and writes its JSON report to `artifacts/p1-data-audit.json`.
