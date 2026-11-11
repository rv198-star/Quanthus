"""Same-original-body holder representation repair, never value priority.

Only components whose old latest records match unique new coordinates, exact
original artifact/body identity and all scalar/text fields may gain a new
representation version. Old open rows close at the real governance clock; their
values and provenance remain intact. This is a bounded integration TMP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base22_bounded import Budget, GlobalScan
from .base22_holder_components_v2 import COMPONENT_KEYS
from .base231_domain_pack import schemas
from .base231_native_delta import FRAME, HOLDING, META, holder_views


def revision_views(scan: GlobalScan) -> None:
    holder_views(scan)
    coarse = COMPONENT_KEYS[:-2]
    match = " AND ".join("o." + k + " IS NOT DISTINCT FROM n." + k for k in coarse)
    excluded = META | {"share_nature", "holder_category", "holding_percentage_unit"}
    unchanged = " AND ".join("o." + k + " IS NOT DISTINCT FROM n." + k
                             for k in schemas()[HOLDING].names if k not in excluded)
    provenance = " AND ".join("o." + k + " IS NOT DISTINCT FROM n." + k for k in (
        "record_source", "source_record_id", "source_artifact_sha256"))
    condition = (unchanged + " AND " + provenance
                 + " AND o.knowledge_to IS NULL AND n.knowledge_to IS NULL"
                 + " AND n.knowledge_from>o.knowledge_from")
    # The complete new source matrix is already certified. Repair only a
    # uniquely matched component; other old components stay untouched. No
    # absence is converted into an exit and no entire mixed-provenance frame is
    # replaced. One scoped typed dependency serves both output views.
    scan.connection.execute("CREATE TEMP TABLE representation_prior AS SELECT * "
                            "FROM prior_holding WHERE __n=1")
    scan.connection.execute("CREATE TEMP TABLE representation_next AS SELECT * FROM next_holding")
    coarse_cols = ",".join(coarse)
    scan.connection.execute("CREATE TEMP TABLE representation_old_census AS SELECT " + coarse_cols
                            + ",count(*) AS __count FROM representation_prior GROUP BY "
                            + coarse_cols)
    scan.connection.execute("CREATE TEMP TABLE representation_new_census AS SELECT " + coarse_cols
                            + ",count(*) AS __count FROM representation_next GROUP BY "
                            + coarse_cols)
    count_old = " AND ".join("o." + k + " IS NOT DISTINCT FROM x." + k for k in coarse)
    count_new = " AND ".join("n." + k + " IS NOT DISTINCT FROM y." + k for k in coarse)
    changed = " OR ".join("o." + k + " IS DISTINCT FROM n." + k for k in (
        "share_nature", "holder_category", "holding_percentage_unit"))
    old_keys = ",".join("o." + k for k in COMPONENT_KEYS)
    scan.connection.execute("CREATE TEMP TABLE representation_pairs AS SELECT " + old_keys
                            + ",n.knowledge_from AS __clock FROM representation_prior o JOIN "
                            "representation_next n ON " + match + " JOIN representation_old_census "
                            "x ON " + count_old + " JOIN representation_new_census y ON "
                            + count_new
                            + " WHERE x.__count=1 AND y.__count=1 AND " + condition
                            + " AND (" + changed + ")")
    frame_cols = ",".join(FRAME)
    scan.connection.execute("CREATE TEMP VIEW representation_safe_frames AS SELECT DISTINCT "
                            + frame_cols + " FROM representation_pairs")
    pairs_new = " AND ".join("n." + k + " IS NOT DISTINCT FROM f." + k for k in coarse)
    pairs_old = " AND ".join("o." + k + " IS NOT DISTINCT FROM f." + k for k in COMPONENT_KEYS)
    names = schemas()[HOLDING].names
    columns = ",".join("n." + k for k in names)
    scan.connection.execute("CREATE TEMP VIEW representation_revisions AS SELECT " + columns
                            + " FROM representation_next n JOIN representation_pairs f ON "
                            + pairs_new)
    columns_old = ",".join("f.__clock AS knowledge_to" if k == "knowledge_to"
                           else "o." + k for k in names)
    scan.connection.execute("CREATE TEMP VIEW representation_closed_old AS SELECT " + columns_old
                            + " FROM representation_prior o JOIN representation_pairs f ON "
                            + pairs_old)


def replay_representation_tmp(root: Path, budget: Budget) -> dict[str, Any]:
    """Verify and rebuild owned TMP rows offline; no full-Base/source-admission claim."""
    import json
    from pathlib import Path

    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from .base22 import file_sha
    from .base22_bounded import BoundedError
    from .base22_sources import _tree
    from .base231_domain_pack import _local

    root = Path(root)
    body = json.loads((root / 'representation-TMP.json').read_bytes())
    if (body.get('kind') != 'p10_same_original_holder_representation_TMP_v1'
            or body.get('business_through') != '2026-09-30'
            or body.get('lifecycle') != 'TMP'
            or body.get('consumer_admission') != 'NOT_GRANTED'
            or body.get('whole_base_acceptance') is not False
            or body.get('transaction_equivalence_granted') is not False
            or body.get('old_values_replaced') != 0 or body.get('old_history_deleted') != 0):
        raise BoundedError('HOLDER_REPRESENTATION_TMP_PURPOSE_CHANGED')
    items = body['inputs'] + body['outputs']
    expected = {r['path'] for r in items} | {'representation-TMP.json'}
    if len(expected) != len(items) + 1 or _tree(root) != expected:
        raise BoundedError('HOLDER_REPRESENTATION_TMP_PHYSICAL_SET_CHANGED')
    for item in items:
        path = _local(root, item['path'])
        if (path.stat().st_nlink != 1 or path.stat().st_size != item['bytes']
                or file_sha(path) != item['sha256']):
            raise BoundedError('HOLDER_REPRESENTATION_TMP_BYTES_CHANGED')
    roles: dict[str, list[Path]] = {}
    for item in body['inputs']:
        roles.setdefault(item['role'], []).append(_local(root, item['path']))
    if set(roles) != {'old', 'new.parquet', 'future.parquet'} or any(
        len(roles[k]) != 1 for k in ('new.parquet', 'future.parquet')
    ):
        raise BoundedError('HOLDER_REPRESENTATION_TMP_INPUT_ROLES_CHANGED')
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add('old_holding', roles['old'])
        scan.add('new_holding_all', roles['new.parquet'])
        scan.add('future_claims', roles['future.parquet'])
        scan.connection.execute('CREATE VIEW new_holding AS SELECT n.* FROM new_holding_all n '
                                'WHERE NOT EXISTS(SELECT 1 FROM future_claims f WHERE '
                                'n.source_record_id=f.source_record_id AND '
                                'n.source_artifact_sha256=f.source_artifact_sha256 AND '
                                'n.observation_start=f.observation_start)')
        revision_views(scan)
        frames = scan.connection.execute('SELECT count(*) FROM representation_safe_frames'
                                         ).fetchone()
        if frames is None or frames[0] != body['frames']:
            raise BoundedError('HOLDER_REPRESENTATION_TMP_FRAME_CENSUS_CHANGED')
        if {r['path'] for r in body['outputs']} != {
            'representation_closed_old.parquet', 'representation_revisions.parquet',
        }:
            raise BoundedError('HOLDER_REPRESENTATION_TMP_OUTPUT_SET_CHANGED')
        for item in body['outputs']:
            view = item['path'].removesuffix('.parquet')
            destination = budget.root / item['path']
            count = 0
            reader = scan.connection.execute('SELECT * FROM ' + view + ' ORDER BY '
                'company_id,security_id,holding_type,observation_start,holder_rank,'
                'share_nature,holding_percentage_unit').to_arrow_reader(16384)
            with pq.ParquetWriter(destination, schemas()[HOLDING], compression='zstd') as writer:
                for batch in reader:
                    budget.check(reserve=batch.nbytes * 2)
                    typed = pa.Table.from_batches([batch]).cast(schemas()[HOLDING], safe=True)
                    writer.write_table(typed)
                    count += typed.num_rows
            if count != item['rows'] or file_sha(destination) != item['sha256']:
                raise BoundedError('HOLDER_REPRESENTATION_TMP_REPLAY_CHANGED')
    return {'status': 'PASS_OFFLINE_REPRESENTATION_TMP_REPLAY', 'frames': frames[0],
            'outputs': body['outputs'], 'old_values_replaced': 0, 'whole_base_acceptance': False}
