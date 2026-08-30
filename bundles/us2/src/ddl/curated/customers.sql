-- =============================================================================
-- us2 CURATED LAYER - table definitions
-- =============================================================================
-- >>> PLACEHOLDER <<<
--
-- Curated tables in this repo are created by the job that writes them
-- (saveAsTable with an explicit schema), so this file is a REFERENCE for what
-- the curated contract is - the shape downstream consumers may rely on.
--
-- Use it two ways:
--   1. as the reviewed definition of the us2 curated contract;
--   2. as explicit DDL, if your team prefers create-then-insert over
--      saveAsTable. If you do, run it from a task ahead of the curate task.
--
-- Written into: edp_curated_<env>.us2
-- =============================================================================

CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.customers') (
    customers_id   STRING        NOT NULL COMMENT 'Natural key. Deduplicated on this.',
    event_ts      TIMESTAMP     COMMENT 'Business event time, parsed from the landed payload.',
    status        STRING        COMMENT 'NULL when the source sent a value we do not recognise.',
    amount        DECIMAL(18,2),
    currency      STRING        COMMENT 'ISO code, upper-cased. Defaults to USD when absent.',
    ingested_at   TIMESTAMP     COMMENT 'When the landing layer wrote the source row.'
  )
USING DELTA
COMMENT 'Curated customers for us2. Written by the edp_us2 bundle.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);
