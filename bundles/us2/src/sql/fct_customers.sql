-- =============================================================================
-- us2 datamart: fct_customers
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us2 fact definition. <<<
--
-- Full rebuild. us2 volume is small enough that a rebuild is simpler and more
-- obviously correct than an incremental merge; revisit when that stops being
-- true. The trade-off is deliberate and written down rather than assumed.
-- =============================================================================

CREATE OR REPLACE TABLE
  IDENTIFIER(:catalog || '.' || :schema || '.fct_customers')
COMMENT 'Fact table for us2 customers, keyed to dim_customers.'
AS
SELECT
  c.customers_id,
  d.customers_key,
  CAST(date_format(c.event_ts, 'yyyyMMdd') AS INT) AS date_key,
  c.event_ts,
  c.status,
  c.amount,
  c.currency,
  c.ingested_at,
  current_timestamp() AS dw_updated_at
FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.customers') AS c
-- LEFT JOIN, not INNER: a record whose dimension row has not landed yet must
-- still appear in the fact with a null key, so its value is not silently lost.
-- The curated quality gate is what alerts on the orphan.
LEFT JOIN IDENTIFIER(:catalog || '.' || :schema || '.dim_customers') AS d
  ON c.customers_id = d.customers_id
WHERE c.customers_id IS NOT NULL;
