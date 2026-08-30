-- =============================================================================
-- us2 datamart: dim_customers
-- =============================================================================
-- >>> PLACEHOLDER - replace with the ported us2 mart definition. <<<
--
-- Parameters are BOUND (:catalog, :curated_catalog, :schema), never concatenated.
-- The same file builds a developer sandbox mart and the production mart:
--   sandbox : edp_datamart_nonprod.jsmith_us2.dim_customers
--   prod    : edp_datamart_prod.us2.dim_customers
--
-- IDENTIFIER() is what lets a bound parameter form part of an object name - a
-- plain :param cannot appear where SQL expects an identifier.
--
-- Note the THREE-catalog reference: this reads from the curated catalog and
-- writes to the datamart catalog. Cross-catalog joins are normal here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.dim_customers') (
    customers_key      BIGINT   GENERATED ALWAYS AS IDENTITY,
    customers_id       STRING   NOT NULL COMMENT 'Natural key from the source system.',
    status            STRING,
    currency          STRING,
    source_updated_at TIMESTAMP,
    dw_updated_at     TIMESTAMP
  )
USING DELTA
COMMENT 'Dimension for us2 customers. Built by the edp_us2 bundle.';

-- MERGE rather than CREATE OR REPLACE so customers_key stays stable across runs.
-- A surrogate key that changed nightly would break every saved report using it.
MERGE INTO IDENTIFIER(:catalog || '.' || :schema || '.dim_customers') AS t
USING (
  SELECT customers_id, status, currency, event_ts AS source_updated_at
  FROM IDENTIFIER(:curated_catalog || '.' || :schema || '.customers')
) AS s
  ON t.customers_id = s.customers_id
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
    customers_id, status, currency, source_updated_at, dw_updated_at
  ) VALUES (
    s.customers_id, s.status, s.currency, s.source_updated_at, current_timestamp()
  );
