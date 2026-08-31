-- =============================================================================
-- us3 DATAMART LAYER - table definitions
-- =============================================================================
-- >>> PLACEHOLDER <<<
--
-- THIS FILE IS EXECUTED. us3_migrate runs it on every deploy, before the
-- datamart job. It owns the SHAPE of the mart tables - the contract business
-- consumers rely on.
--
-- src/sql/dim_events.sql and src/sql/fct_events.sql fill them with DATA
-- (MERGE and INSERT OVERWRITE), and neither redefines the table. That is why
-- neither uses CREATE OR REPLACE TABLE: it would rebuild the table nightly from
-- a SELECT and quietly discard everything declared here.
--
-- Changing a line here affects only environments that do not have the table yet.
-- Existing ones need a numbered file in ../migrations/, in the same PR.
--
-- Written into: edp_datamart_<env>.us3
-- Granted to:   edp-business-analysts in prod, by publish_marts.py
-- =============================================================================

-- See src/sql/dim_events.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.dim_events') (
    events_key      BIGINT    COMMENT 'Surrogate key. Stable across runs.',
    events_id       STRING    NOT NULL,
    status            STRING,
    currency          STRING,
    source_updated_at TIMESTAMP,
    dw_updated_at     TIMESTAMP
  )
USING DELTA
COMMENT 'Dimension contract for us3 events.';

-- See src/sql/fct_events.sql for the authoritative definition.
CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.fct_events') (
    events_id    STRING,
    events_key   BIGINT    COMMENT 'NULL when the dimension row has not landed yet.',
    date_key       INT,
    event_ts       TIMESTAMP,
    status         STRING,
    amount         DECIMAL(18,2),
    currency       STRING,
    ingested_at    TIMESTAMP,
    dw_updated_at  TIMESTAMP
  )
USING DELTA
COMMENT 'Fact contract for us3 events.';
