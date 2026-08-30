-- =============================================================================
-- ops.audit  -  append-only observability for every use case
-- =============================================================================
-- Three grains:
--   job_run              one row per (run_id, task_key)  - did this task work?
--   table_load           one row per table written       - how much data moved?
--   data_quality_result  one row per expectation         - was the data any good?
--
-- All append-only. A retry appends rather than rewrites, so these are safe to
-- read from a dashboard while a load is in flight.
--
-- {{catalog}} and {{schema}} are substituted by bootstrap_ops_framework.py.
-- Every statement is idempotent so the bootstrap job runs on every deploy - that
-- is how a new column reaches all three environments the same way code does.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.job_run (
  run_id            STRING    COMMENT 'Databricks job run id.',
  job_id            STRING    COMMENT 'Databricks job id.',
  task_key          STRING    COMMENT 'Task within the job.',
  use_case          STRING    COMMENT 'us1..us5, or landing / platform.',
  layer             STRING    COMMENT 'landing | curated | datamart | recon | platform',
  env               STRING    COMMENT 'nonprod | preprod | prod',
  bundle_target     STRING    COMMENT 'Bundle target deployed from: dev | nonprod | preprod | prod.',
  status            STRING    COMMENT 'RUNNING | SUCCESS | FAILED | SKIPPED',
  started_at        TIMESTAMP,
  ended_at          TIMESTAMP,
  duration_seconds  DOUBLE,
  error_message     STRING    COMMENT 'Truncated exception summary.',
  error_detail      STRING    COMMENT 'Truncated traceback.',
  context_tags      MAP<STRING, STRING> COMMENT 'Includes sandbox=true for developer runs.'
)
USING DELTA
COMMENT 'One row per task execution. Written by dab_common.audit.audited_run().'
PARTITIONED BY (env);

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.table_load (
  run_id          STRING,
  use_case        STRING,
  source_id       STRING    COMMENT 'FK to ops.config.landing_source, where applicable.',
  target_table    STRING    COMMENT 'Fully-qualified table written.',
  load_strategy   STRING,
  rows_written    BIGINT,
  watermark_from  STRING    COMMENT 'Watermark at the start of this load.',
  watermark_to    STRING    COMMENT 'Watermark after this load committed.',
  status          STRING,
  env             STRING,
  loaded_at       TIMESTAMP
)
USING DELTA
COMMENT 'One row per table load. Answers "did last night actually land?".'
PARTITIONED BY (env);

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.data_quality_result (
  run_id                  STRING,
  env                     STRING,
  use_case                STRING,
  table_name              STRING,
  expectation_name        STRING,
  expectation_predicate   STRING,
  severity                STRING    COMMENT 'warn | error',
  rows_evaluated          BIGINT,
  rows_failed             BIGINT,
  passed                  BOOLEAN,
  evaluated_at            TIMESTAMP
)
USING DELTA
COMMENT 'One row per expectation evaluated. Written by dab_common.quality.evaluate().'
PARTITIONED BY (env);
