-- =============================================================================
-- us5 datamart: fct_settlement
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us5 fact definition. <<<
--
-- Full rebuild. us5 volume is small enough that a rebuild is simpler and more
-- obviously correct than an incremental merge; revisit when that stops being
-- true. The trade-off is deliberate and written down rather than assumed.
-- =============================================================================

CREATE OR REPLACE TABLE
  IDENTIFIER(:catalog || '.' || :schema || '.fct_settlement')
COMMENT 'Fact table for us5 settlement, keyed to dim_settlement.'
AS
SELECT
  c.settlement_id,
  d.settlement_key,
  CAST(date_format(c.event_ts, 'yyyyMMdd') AS INT) AS date_key,
  c.event_ts,
  c.status,
  c.amount,
  c.currency,
  c.ingested_at,
  current_timestamp() AS dw_updated_at
FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.settlement') AS c
-- LEFT JOIN, not INNER: a record whose dimension row has not landed yet must
-- still appear in the fact with a null key, so its value is not silently lost.
-- The curated quality gate is what alerts on the orphan.
LEFT JOIN IDENTIFIER(:catalog || '.' || :schema || '.dim_settlement') AS d
  ON c.settlement_id = d.settlement_id
WHERE c.settlement_id IS NOT NULL;
