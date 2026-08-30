-- =============================================================================
-- us5 datamart: dim_settlement
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us5 mart definition. <<<
--
-- Parameters are BOUND (:catalog, :curated_catalog, :schema), never concatenated.
-- The same file builds a developer sandbox mart and the production mart:
--   sandbox : edp_datamart_nonprod.jsmith_us5.dim_settlement
--   prod    : edp_datamart_prod.us5.dim_settlement
--
-- IDENTIFIER() is what lets a bound parameter form part of an object name - a
-- plain :param cannot appear where SQL expects an identifier.
--
-- Note the THREE-catalog reference: this reads from the curated catalog and
-- writes to the datamart catalog. Cross-catalog joins are normal here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.dim_settlement') (
    settlement_key      BIGINT   GENERATED ALWAYS AS IDENTITY,
    settlement_id       STRING   NOT NULL COMMENT 'Natural key from the source system.',
    status            STRING,
    currency          STRING,
    source_updated_at TIMESTAMP,
    dw_updated_at     TIMESTAMP
  )
USING DELTA
COMMENT 'Dimension for us5 settlement. Built by the edp_us5 bundle.';

-- MERGE rather than CREATE OR REPLACE so settlement_key stays stable across runs.
-- A surrogate key that changed nightly would break every saved report using it.
MERGE INTO IDENTIFIER(:catalog || '.' || :schema || '.dim_settlement') AS t
USING (
  SELECT settlement_id, status, currency, event_ts AS source_updated_at
  FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.settlement')
) AS s
  ON t.settlement_id = s.settlement_id
-- IS DISTINCT FROM, not <>: a value changing to or from NULL is a real change,
-- and <> evaluates to NULL and silently skips the update.
WHEN MATCHED AND (
       t.status   IS DISTINCT FROM s.status
    OR t.currency IS DISTINCT FROM s.currency
  ) THEN UPDATE SET
    t.status            = s.status,
    t.currency          = s.currency,
    t.source_updated_at = s.source_updated_at,
    t.dw_updated_at     = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
    settlement_id, status, currency, source_updated_at, dw_updated_at
  ) VALUES (
    s.settlement_id, s.status, s.currency, s.source_updated_at, current_timestamp()
  );
