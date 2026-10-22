"""DWD projection for the admitted Base 2.4 native five-minute supplement."""

from __future__ import annotations

import re


class Native5mDwdError(ValueError):
    """Stable adapter contract failure."""


def _relation(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise Native5mDwdError("RELATION_NAME_INVALID")
    return value


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def dwd_native5m_query(
    *,
    projected_relation: str = "projected_native5m",
    listing_relation: str = "dwd_listing",
    calendar_relation: str = "dwd_trading_calendar",
    knowledge_cutoff: str = "2026-09-30T23:59:59+08:00",
) -> str:
    """Resolve exact observations to DWD listing/session identities.

    The source timestamp is the supplier's interval-end label. The exceptional
    13:00 label is the morning close and therefore maps to 11:30 Shanghai time.
    """
    projected = _relation(projected_relation)
    listing = _relation(listing_relation)
    calendar = _relation(calendar_relation)
    cutoff = _literal(knowledge_cutoff)
    return f"""
WITH source AS (
  SELECT p.*,
         timezone('Asia/Shanghai',CASE WHEN cast(p.ts AS TIME)=TIME '13:00:00'
                  THEN p.ts-INTERVAL 90 MINUTE ELSE p.ts END) minute_end
  FROM {projected} p
  WHERE p.bar_size='5m'
), resolved AS (
  SELECT s.*,l.listing_id,l.venue_id,c.calendar_session_id
  FROM source s
  JOIN {listing} l ON l.listing_code=s.stock_code
  JOIN {calendar} c ON c.venue_id=l.venue_id AND c.trade_date=s.market_date
       AND c.session_type IN ('REGULAR_DAY','day') AND c.is_open
       AND c.knowledge_from<=TIMESTAMPTZ {cutoff}
       AND (c.knowledge_to IS NULL OR TIMESTAMPTZ {cutoff}<c.knowledge_to)
  WHERE l.effective_from<=s.minute_end
    AND (l.effective_to IS NULL OR s.minute_end<l.effective_to)
    AND l.knowledge_from<=TIMESTAMPTZ {cutoff}
    AND (l.knowledge_to IS NULL OR TIMESTAMPTZ {cutoff}<l.knowledge_to)
)
SELECT 'base24_native5m_exact' record_source,
       'partition='||input_partition_id||'&stock_code='||stock_code||'&ts='||
         strftime(ts,'%Y-%m-%dT%H:%M:%S') source_record_id,
       'base24_native5m_exact@'||substr(replace(input_artifact_sha256,'sha256:',''),1,16)
         source_revision,
       input_artifact_sha256 source_artifact_sha256,
       acquired_at knowledge_from,NULL::TIMESTAMPTZ knowledge_to,
       minute_end-INTERVAL 5 MINUTE observation_start,minute_end observation_end,
       listing_id,calendar_session_id,'5m' interval_size,'UNADJUSTED' price_basis,
       'CNY' currency,open,high,low,"close",volume,amount turnover,
       NULL::BIGINT trade_count,
       strftime(minute_end-INTERVAL 5 MINUTE,'%Y') observation_start_year,
       strftime(minute_end-INTERVAL 5 MINUTE,'%m') observation_start_month
FROM resolved
"""


def merged_default_bar_query(
    *,
    predecessor_relation: str = "predecessor_bar",
    supplement_relation: str = "native5m_supplement",
) -> str:
    """Keep 1d and qualified 5m, replacing each supplemented security-day whole."""
    predecessor = _relation(predecessor_relation)
    supplement = _relation(supplement_relation)
    return f"""
WITH replacement_days AS (
  SELECT DISTINCT listing_id,
         cast(timezone('Asia/Shanghai',observation_start) AS DATE) business_date
  FROM {supplement}
), retained AS (
  SELECT p.* FROM {predecessor} p
  WHERE p.interval_size='1d'
     OR (p.interval_size='5m' AND NOT EXISTS (
       SELECT 1 FROM replacement_days r
       WHERE r.listing_id=p.listing_id
         AND r.business_date=cast(timezone('Asia/Shanghai',p.observation_start) AS DATE)
     ))
)
SELECT * FROM retained
UNION ALL BY NAME
SELECT * FROM {supplement}
"""
