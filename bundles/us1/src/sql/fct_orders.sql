-- =============================================================================
-- us1 datamart: fct_orders
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us1 fact definition. <<<
--
-- Full rebuild. us1 volume is small enough that a rebuild is simpler and more
-- obviously correct than an incremental merge; revisit when that stops being
-- true. The trade-off is deliberate and written down rather than assumed.
--
-- INSERT OVERWRITE, not CREATE OR REPLACE TABLE. The table is created and
-- evolved by us1_migrate from ../ddl/datamart/marts.sql and ../ddl/migrations/.
-- CREATE OR REPLACE would rebuild the table from this SELECT every night, so the
-- declared column comments, types and any applied migration would be discarded -
-- while ops.config.schema_migration still recorded the migration as applied.
--
-- BY NAME so a column added by a migration does not depend on the order of this
-- SELECT, and a column this SELECT forgets fails loudly instead of shifting
-- every value one position to the left.
-- =============================================================================

INSERT OVERWRITE IDENTIFIER(:catalog || '.' || :schema || '.fct_orders')
BY NAME
SELECT
  c.orders_id,
  d.orders_key,
  CAST(date_format(c.event_ts, 'yyyyMMdd') AS INT) AS date_key,
  c.event_ts,
  c.status,
  c.amount,
  c.currency,
  c.ingested_at,
  current_timestamp() AS dw_updated_at
FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.orders') AS c
-- LEFT JOIN, not INNER: a record whose dimension row has not landed yet must
-- still appear in the fact with a null key, so its value is not silently lost.
-- The curated quality gate is what alerts on the orphan.
LEFT JOIN IDENTIFIER(:catalog || '.' || :schema || '.dim_orders') AS d
  ON c.orders_id = d.orders_id
WHERE c.orders_id IS NOT NULL;
