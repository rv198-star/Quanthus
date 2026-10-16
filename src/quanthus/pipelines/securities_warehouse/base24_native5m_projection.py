"""Bounded ODS projection for exact Base 2.4 native-five-minute acquisition."""

from __future__ import annotations

import re


class Native5mProjectionError(ValueError):
    """Projection contract error without source prices."""


def _relation(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise Native5mProjectionError("RELATION_NAME_INVALID")
    return value


def projection_query(
    *,
    exact_relation: str = "exact_rows",
    identity_relation: str = "source_identity",
    required_relation: str = "required_missing",
) -> str:
    """Return one scan/join projection; required gaps exclude incidental responses.

    ``exact_relation`` is the immutable acquired Parquet scan with a ``filename``
    column. ``identity_relation`` binds each file to its partition/hash/acquisition
    clock. ``required_relation`` is the frozen listing-session gap denominator.
    """
    exact = _relation(exact_relation)
    identity = _relation(identity_relation)
    required = _relation(required_relation)
    return f"""
WITH source AS (
  SELECT e.*,substr(e.code,4) stock_code,
         timezone('Asia/Shanghai',e.timestamp) local_timestamp,
         try_cast(i.acquired_at AS TIMESTAMPTZ) acquired_clock,
         i.partition_id,i.source_artifact_sha256
  FROM {exact} e JOIN {identity} i USING(filename)
), bounded AS (
  SELECT s.* FROM source s JOIN {required} r
    ON r.stock_code=s.stock_code AND r.trade_date=cast(s.local_timestamp AS DATE)
)
SELECT stock_code,local_timestamp AS ts,cast(local_timestamp AS DATE) market_date,
       '5m' bar_size,source_values,
       cast(open AS DECIMAL(38,10)) open,cast(high AS DECIMAL(38,10)) high,
       cast(low AS DECIMAL(38,10)) low,cast(close AS DECIMAL(38,10)) AS "close",
       cast(volume AS DECIMAL(38,10)) volume,cast(amount AS DECIMAL(38,10)) amount,
       CASE WHEN acquired_clock IS NULL
              THEN error('BASE24_NATIVE5M_ACQUIRED_AT_INVALID')
            ELSE acquired_clock END acquired_at,
       partition_id input_partition_id,
       'sha256:' || source_artifact_sha256 input_artifact_sha256,
       year(local_timestamp) observation_year,month(local_timestamp) observation_month
FROM bounded
"""
