-- =============================================================================
-- us5 DATAMART LAYER - table definitions
-- =============================================================================
-- >>> PLACEHOLDER <<<
--
-- THIS FILE IS EXECUTED. us5_migrate runs it on every deploy, before the
-- datamart job. It owns the SHAPE of the mart tables - the contract business
-- consumers rely on.
--
-- src/sql/dim_settlement.sql and src/sql/fct_settlement.sql fill them with DATA
-- (MERGE and INSERT OVERWRITE), and neither redefines the table. That is why
-- neither uses CREATE OR REPLACE TABLE: it would rebuild the table nightly from
-- a SELECT and quietly discard everything declared here.
--
-- Changing a line here affects only environments that do not have the table yet.
-- Existing ones need a numbered file in ../migrations/, in the same PR.
--
-- Written into: edp_datamart_<env>.us5
-- Granted to:   edp-business-analysts in prod, by publish_marts.py
-- =============================================================================

-- See src/sql/dim_settlement.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.dim_settlement') (
    settlement_key      BIGINT    COMMENT 'Surrogate key. Stable across runs.',
    settlement_id       STRING    NOT NULL,
    status            STRING,
    currency          STRING,
    source_updated_at TIMESTAMP,
    dw_updated_at     TIMESTAMP
  )
USING DELTA
COMMENT 'Dimension contract for us5 settlement.';

-- See src/sql/fct_settlement.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.fct_settlement') (
    settlement_id    STRING,
    settlement_key   BIGINT    COMMENT 'NULL when the dimension row has not landed yet.',
    date_key       INT,
    event_ts       TIMESTAMP,
    status         STRING,
    amount         DECIMAL(18,2),
    currency       STRING,
    ingested_at    TIMESTAMP,
    dw_updated_at  TIMESTAMP
  )
USING DELTA
COMMENT 'Fact contract for us5 settlement.';
