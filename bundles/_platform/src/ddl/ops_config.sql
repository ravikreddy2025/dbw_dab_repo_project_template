-- =============================================================================
-- ops.config  -  the metadata that drives the shared landing framework
-- =============================================================================
-- Onboarding a Kafka topic or an Oracle table is a ROW here, seeded from
-- bundles/landing/conf/<use_case>/sources.yml by the
-- landing_seed_source_registry job. It is never typed into a SQL editor and it
-- is never a new notebook.
--
-- These tables live in ops rather than landing because they are operational
-- metadata shared across use cases, not anyone's data.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.landing_source (
  source_id         STRING  NOT NULL COMMENT 'Stable business key, e.g. us1_kfk_orders. Never reused.',
  use_case          STRING  NOT NULL COMMENT 'us1..us5 - which schema in the landing catalog it lands into.',
  source_system     STRING  NOT NULL COMMENT 'oracle | kafka',
  source_object     STRING  NOT NULL COMMENT 'Oracle: SCHEMA.TABLE. Kafka: topic name.',
  target_table      STRING  NOT NULL COMMENT 'Delta table name within the use-case landing schema.',
  load_strategy     STRING  NOT NULL COMMENT 'full | incremental | cdc_stream',
  watermark_column  STRING           COMMENT 'Required when load_strategy = incremental.',
  primary_keys      STRING           COMMENT 'Comma-separated PKs, used by the curated layer for dedup.',
  secret_scope      STRING           COMMENT 'Secret scope holding credentials for this source.',
  options           MAP<STRING, STRING> COMMENT 'Framework knobs: partition_column, starting_offsets, ...',
  is_active         BOOLEAN NOT NULL DEFAULT true COMMENT 'false retires a source without losing history.',
  owner_email       STRING           COMMENT 'Who to call when this source breaks.',
  created_at        TIMESTAMP        DEFAULT current_timestamp(),
  updated_at        TIMESTAMP        DEFAULT current_timestamp()
)
USING DELTA
COMMENT 'Landing source registry. Seeded per environment from git; never hand-edited.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.landing_watermark (
  source_id        STRING    NOT NULL COMMENT 'FK to landing_source.source_id',
  watermark_value  STRING    NOT NULL COMMENT 'ISO-8601 timestamp or monotonic key, stored as text.',
  updated_at       TIMESTAMP NOT NULL DEFAULT current_timestamp()
)
USING DELTA
COMMENT 'High-water marks. Advanced only after a successful commit, and never backwards.';
