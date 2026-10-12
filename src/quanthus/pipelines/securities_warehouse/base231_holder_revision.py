"""Same-original-body holder representation repair, never value priority.

Only frames whose old latest components match unique new coordinates, exact
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
        "record_source", "source_record_id", "source_artifact_sha256", "source_revision"))
    condition = (unchanged + " AND " + provenance
                 + " AND o.knowledge_to IS NULL AND n.knowledge_to IS NULL"
                 + " AND n.knowledge_from>o.knowledge_from")
    frame_match = " AND ".join("o." + k + " IS NOT DISTINCT FROM f." + k for k in FRAME)
    frame_new = " AND ".join("n." + k + " IS NOT DISTINCT FROM f." + k for k in FRAME)
    # Materialize this one scoped dependency once. Correlated re-expansion of
    # prior window/sort views consumed unnecessary memory on real frames.
    scan.connection.execute("CREATE TEMP TABLE representation_prior AS SELECT * "
                            "FROM prior_holding WHERE __n=1")
    scan.connection.execute("CREATE TEMP TABLE representation_next AS SELECT * FROM next_holding")
    keys = ",".join("o." + k for k in COMPONENT_KEYS)
    scan.connection.execute("CREATE TEMP TABLE representation_matches AS SELECT " + keys
                            + ",count(*) FILTER(WHERE " + condition + ") AS __matches "
                            "FROM representation_prior o LEFT JOIN representation_next n ON "
                            + match + " GROUP BY " + keys)
    scan.connection.execute("CREATE TEMP TABLE representation_candidates AS SELECT * "
                            "FROM held_frames")
    clock_join = " AND ".join("c." + k + " IS NOT DISTINCT FROM f." + k for k in FRAME)
    clock_cols = ",".join(FRAME)
    scan.connection.execute("CREATE TEMP TABLE representation_clocks AS SELECT " + clock_cols
                            + ",min(knowledge_from) AS __clock,max(knowledge_from) AS __last "
                            "FROM representation_next GROUP BY " + clock_cols)
    scan.connection.execute("CREATE TEMP TABLE representation_safe_frames AS SELECT f.*,c.__clock "
                            "FROM representation_candidates f JOIN representation_clocks c ON "
                            + clock_join + " WHERE c.__clock=c.__last AND NOT EXISTS(SELECT 1 "
                            "FROM representation_matches o WHERE " + frame_match
                            + " AND o.__matches<>1) AND NOT EXISTS(SELECT 1 "
                            "FROM representation_next n WHERE " + frame_new
                            + " AND NOT EXISTS(SELECT 1 FROM representation_prior o WHERE "
                            + frame_match + " AND " + provenance + "))")
    names = schemas()[HOLDING].names
    columns = ",".join("n." + k for k in names)
    scan.connection.execute("CREATE TEMP VIEW representation_revisions AS SELECT " + columns
                            + " FROM representation_next n JOIN representation_safe_frames f ON "
                            + frame_new)
    columns_old = ",".join("f.__clock AS knowledge_to" if k == "knowledge_to"
                           else "o." + k for k in names)
    scan.connection.execute("CREATE TEMP VIEW representation_closed_old AS SELECT " + columns_old
                            + " FROM representation_prior o JOIN representation_safe_frames f ON "
                            + frame_match)


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
