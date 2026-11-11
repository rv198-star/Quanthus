"""Bounded integration of qualified September event and identity observations.

The caller binds exact qualified source files and native business partitions.
Only exact official-body disclosure successors may close native open knowledge
intervals. Other scopes append disjoint versions or reuse identical atoms.
This is a domain TMP operation, not whole-image or consumer admission.
"""

from __future__ import annotations

from typing import Any

from .base22_bounded import BoundedError, GlobalScan, identifier
from .base231_disclosure_body import CONTRACT_ID

TABLES = {
    "dwd_disclosure",
    "dwd_disclosure_subject",
    "dwd_corporate_action",
    "dwd_corporate_action_term",
    "dwd_security_identifier",
    "dwd_adjustment_factor",
    "dwd_adjustment_factor_input",
    "dwd_company_identifier",
    "dwd_listed_company",
    "dwd_security",
    "dwd_listing",
    "dwd_shareholding_change",
}


def observation_views(
    scan: GlobalScan,
    table: str,
    schema: Any,
    rule: dict[str, Any],
) -> dict[str, int]:
    """Use ``before``/``incoming_raw``; retain native atoms except authorized ends.

    Qualification is an earlier stage, not inferred from a provider label here.
    Disclosure revision also requires stable issuer/document/language identity.
    Exact duplicate rows collapse; competing rows at one version fail closed.
    """
    if table not in TABLES or "knowledge_from" not in rule["version_key"]:
        raise BoundedError("OBSERVATION_TABLE_SCOPE_INVALID")
    c = scan.connection
    columns = [identifier(k) for k in schema.names]
    vk = [identifier(k) for k in rule["version_key"]]
    bk = [identifier(k) for k in rule["business_key"]]
    c.execute("CREATE TEMP VIEW incoming AS SELECT DISTINCT * FROM incoming_raw")
    scan.reject(
        "SELECT 1 FROM incoming GROUP BY " + ",".join(vk) + " HAVING count(*)>1 LIMIT 1",
        "OBSERVATION_INCOMING_VERSION_CONFLICT",
    )
    join = " AND ".join("b." + k + " IS NOT DISTINCT FROM n." + k for k in vk)
    same = " AND ".join("b." + k + " IS NOT DISTINCT FROM n." + k for k in columns)
    scan.reject(
        "SELECT 1 FROM before b JOIN incoming n ON " + join + " WHERE NOT(" + same + ") LIMIT 1",
        "OBSERVATION_VERSION_VALUE_CONFLICT",
    )
    c.execute(
        "CREATE TEMP VIEW added AS SELECT n.* FROM incoming n WHERE NOT EXISTS"
        "(SELECT 1 FROM before b WHERE " + join + ")"
    )
    business_join = " AND ".join("b." + k + " IS NOT DISTINCT FROM n." + k for k in bk)
    scan.reject(
        "SELECT 1 FROM added n JOIN before b ON "
        + business_join
        + " WHERE b.knowledge_from>=n.knowledge_from LIMIT 1",
        "OBSERVATION_RETROACTIVE_VERSION_HELD",
    )
    if table == "dwd_disclosure":
        stable = ["company_id", "source_document_id", "language"]
        conditions = " AND ".join("b." + k + " IS NOT DISTINCT FROM n." + k for k in stable)
        scan.reject(
            "SELECT 1 FROM added n JOIN before b ON "
            + business_join
            + " WHERE NOT("
            + conditions
            + ") OR "
            "n.record_source<>'cninfo.exact_original_disclosure_body' OR "
            "n.source_revision<>'" + CONTRACT_ID + "' LIMIT 1",
            "OBSERVATION_EXACT_BODY_SUCCESSOR_UNQUALIFIED",
        )
    c.execute(
        "CREATE TEMP VIEW clocks AS SELECT *,lead(knowledge_from) OVER "
        "(PARTITION BY " + ",".join(bk) + " ORDER BY knowledge_from) AS next_clock FROM "
        "(SELECT *,TRUE AS is_old FROM before UNION ALL SELECT *,FALSE AS is_old FROM added)"
    )
    scan.reject(
        "SELECT 1 FROM clocks WHERE next_clock IS NOT NULL AND knowledge_to>next_clock LIMIT 1",
        "OBSERVATION_FINITE_END_CONFLICT",
    )
    if table != "dwd_disclosure":
        scan.reject(
            "SELECT 1 FROM clocks WHERE next_clock IS NOT NULL AND knowledge_to IS NULL LIMIT 1",
            "OBSERVATION_UNAUTHORIZED_INTERVAL_CLOSURE",
        )
    c.execute(
        "CREATE TEMP VIEW closed_native_intervals AS SELECT "
        + ",".join(vk)
        + ",next_clock AS after_to FROM clocks WHERE is_old "
        "AND knowledge_to IS NULL AND next_clock IS NOT NULL"
    )
    selected = ",".join(
        "CASE WHEN knowledge_to IS NULL AND next_clock IS NOT NULL THEN next_clock "
        "ELSE knowledge_to END AS knowledge_to"
        if k == "knowledge_to"
        else identifier(k)
        for k in schema.names
    )
    c.execute("CREATE TEMP VIEW integrated AS SELECT " + selected + " FROM clocks")

    def count(view: str) -> int:
        result = c.execute("SELECT count(*) FROM " + identifier(view)).fetchone()
        assert result is not None
        return int(result[0])

    return dict(
        native_rows=count("before"),
        incoming_distinct_rows=count("incoming"),
        incoming_exact_duplicates=count("incoming_raw") - count("incoming"),
        exact_native_versions_reused=count("incoming") - count("added"),
        added_versions=count("added"),
        integrated_rows=count("integrated"),
        closed_native_intervals=count("closed_native_intervals"),
    )
