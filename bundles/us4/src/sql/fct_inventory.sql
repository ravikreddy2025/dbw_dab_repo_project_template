-- =============================================================================
-- us4 datamart: fct_inventory
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us4 fact definition. <<<
--
-- Full rebuild. us4 volume is small enough that a rebuild is simpler and more
-- obviously correct than an incremental merge; revisit when that stops being
-- true. The trade-off is deliberate and written down rather than assumed.
-- =============================================================================

CREATE OR REPLACE TABLE
  IDENTIFIER(:catalog || '.' || :schema || '.fct_inventory')
COMMENT 'Fact table for us4 inventory, keyed to dim_inventory.'
AS
SELECT
  c.inventory_id,
  d.inventory_key,
  CAST(date_format(c.event_ts, 'yyyyMMdd') AS INT) AS date_key,
  c.event_ts,
  c.status,
  c.amount,
  c.currency,
  c.ingested_at,
  current_timestamp() AS dw_updated_at
FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.inventory') AS c
-- LEFT JOIN, not INNER: a record whose dimension row has not landed yet must
-- still appear in the fact with a null key, so its value is not silently lost.
-- The curated quality gate is what alerts on the orphan.
LEFT JOIN IDENTIFIER(:catalog || '.' || :schema || '.dim_inventory') AS d
  ON c.inventory_id = d.inventory_id
WHERE c.inventory_id IS NOT NULL;
