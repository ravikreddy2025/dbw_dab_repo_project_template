-- =============================================================================
-- us4 DATAMART LAYER - table definitions
-- =============================================================================
-- >>> PLACEHOLDER <<<
--
-- The mart tables are created by src/sql/dim_inventory.sql and
-- src/sql/fct_inventory.sql, which the datamart job runs on a SQL warehouse.
-- This file is the reference contract: what business consumers may rely on.
--
-- Written into: edp_datamart_<env>.us4
-- Granted to:   edp-business-analysts in prod, by publish_marts.py
-- =============================================================================

-- See src/sql/dim_inventory.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.dim_inventory') (
    inventory_key      BIGINT    COMMENT 'Surrogate key. Stable across runs.',
    inventory_id       STRING    NOT NULL,
    status            STRING,
    currency          STRING,
    source_updated_at TIMESTAMP,
    dw_updated_at     TIMESTAMP
  )
USING DELTA
COMMENT 'Dimension contract for us4 inventory.';

-- See src/sql/fct_inventory.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.fct_inventory') (
    inventory_id    STRING,
    inventory_key   BIGINT    COMMENT 'NULL when the dimension row has not landed yet.',
    date_key       INT,
    event_ts       TIMESTAMP,
    status         STRING,
    amount         DECIMAL(18,2),
    currency       STRING,
    ingested_at    TIMESTAMP,
    dw_updated_at  TIMESTAMP
  )
USING DELTA
COMMENT 'Fact contract for us4 inventory.';
