"""Same-original-body holder representation repair, never value priority.

Only frames whose old latest components match unique new coordinates, exact
original artifact/body identity and all scalar/text fields may gain a new
representation version. Old open rows close at the real governance clock; their
values and provenance remain intact. This is a bounded integration TMP.
"""
from __future__ import annotations

from .base22_bounded import GlobalScan
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
    scan.connection.execute("CREATE TEMP VIEW representation_safe_frames AS SELECT f.* "
                            "FROM held_frames f WHERE NOT EXISTS(SELECT 1 FROM prior_holding o "
                            "WHERE o.__n=1 AND " + frame_match + " AND (SELECT count(*) "
                            "FROM next_holding n WHERE " + match + " AND " + condition + ")<>1) "
                            "AND NOT EXISTS(SELECT 1 FROM next_holding n WHERE " + frame_new
                            + " AND NOT EXISTS(SELECT 1 FROM prior_holding o WHERE o.__n=1 AND "
                            + frame_match + " AND " + provenance + "))")
    names = schemas()[HOLDING].names
    columns = ",".join("n." + k for k in names)
    scan.connection.execute("CREATE TEMP VIEW representation_revisions AS SELECT " + columns
                            + " FROM next_holding n JOIN representation_safe_frames f ON "
                            + frame_new)
    columns_old = ",".join("(SELECT min(n.knowledge_from) FROM next_holding n WHERE "
                           + frame_new + ") AS knowledge_to" if k == "knowledge_to"
                           else "o." + k for k in names)
    scan.connection.execute("CREATE TEMP VIEW representation_closed_old AS SELECT " + columns_old
                            + " FROM prior_holding o JOIN representation_safe_frames f ON "
                            + frame_match + " WHERE o.__n=1")
