-- =============================================================================
-- us3 datamart: fct_events
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us3 fact definition. <<<
--
-- Full rebuild. us3 volume is small enough that a rebuild is simpler and more
-- obviously correct than an incremental merge; revisit when that stops being
-- true. The trade-off is deliberate and written down rather than assumed.
-- =============================================================================

CREATE OR REPLACE TABLE
  IDENTIFIER(:catalog || '.' || :schema || '.fct_events')
COMMENT 'Fact table for us3 events, keyed to dim_events.'
AS
SELECT
  c.events_id,
  d.events_key,
  CAST(date_format(c.event_ts, 'yyyyMMdd') AS INT) AS date_key,
  c.event_ts,
  c.status,
  c.amount,
  c.currency,
  c.ingested_at,
  current_timestamp() AS dw_updated_at
FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.events') AS c
-- LEFT JOIN, not INNER: a record whose dimension row has not landed yet must
-- still appear in the fact with a null key, so its value is not silently lost.
-- The curated quality gate is what alerts on the orphan.
LEFT JOIN IDENTIFIER(:catalog || '.' || :schema || '.dim_events') AS d
  ON c.events_id = d.events_id
WHERE c.events_id IS NOT NULL;
