-- =============================================================================
-- us1 DATAMART LAYER - table definitions
-- =============================================================================
-- >>> PLACEHOLDER <<<
--
-- The mart tables are created by src/sql/dim_orders.sql and
-- src/sql/fct_orders.sql, which the datamart job runs on a SQL warehouse.
-- This file is the reference contract: what business consumers may rely on.
--
-- Written into: edp_datamart_<env>.us1
-- Granted to:   edp-business-analysts in prod, by publish_marts.py
-- =============================================================================

-- See src/sql/dim_orders.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.dim_orders') (
    orders_key      BIGINT    COMMENT 'Surrogate key. Stable across runs.',
    orders_id       STRING    NOT NULL,
    status            STRING,
    currency          STRING,
    source_updated_at TIMESTAMP,
    dw_updated_at     TIMESTAMP
  )
USING DELTA
COMMENT 'Dimension contract for us1 orders.';

-- See src/sql/fct_orders.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.fct_orders') (
    orders_id    STRING,
    orders_key   BIGINT    COMMENT 'NULL when the dimension row has not landed yet.',
    date_key       INT,
    event_ts       TIMESTAMP,
    status         STRING,
    amount         DECIMAL(18,2),
    currency       STRING,
    ingested_at    TIMESTAMP,
    dw_updated_at  TIMESTAMP
  )
USING DELTA
COMMENT 'Fact contract for us1 orders.';
