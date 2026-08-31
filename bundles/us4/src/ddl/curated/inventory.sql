-- =============================================================================
-- us4 CURATED LAYER - table definitions
-- =============================================================================
-- >>> PLACEHOLDER <<<
--
-- THIS FILE IS EXECUTED. us4_migrate runs it on every deploy, in every
-- environment, before the curate job writes anything. It is not documentation.
--
-- It owns the SHAPE of the curated table. curate.py writes DATA into it and
-- deliberately does NOT pass overwriteSchema, so a transform that stops matching
-- this contract fails loudly instead of silently redefining it.
--
-- CREATE TABLE IF NOT EXISTS means this file only ever builds a table that does
-- not exist yet. Changing a line here has NO effect on an environment that
-- already has the table - that needs a numbered file in ../migrations/, in the
-- same pull request. See docs/04-bundle-authoring.md#changing-an-existing-table.
--
-- Written into: edp_curated_<env>.us4
-- =============================================================================

CREATE TABLE IF NOT EXISTS
  IDENTIFIER(:catalog || '.' || :schema || '.inventory') (
    inventory_id   STRING        NOT NULL COMMENT 'Natural key. Deduplicated on this.',
    event_ts      TIMESTAMP     COMMENT 'Business event time, parsed from the landed payload.',
    status        STRING        COMMENT 'NULL when the source sent a value we do not recognise.',
    amount        DECIMAL(18,2),
    currency      STRING        COMMENT 'ISO code, upper-cased. Defaults to USD when absent.',
    ingested_at   TIMESTAMP     COMMENT 'When the landing layer wrote the source row.'
  )
USING DELTA
COMMENT 'Curated inventory for us4. Written by the edp_us4 bundle.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);
