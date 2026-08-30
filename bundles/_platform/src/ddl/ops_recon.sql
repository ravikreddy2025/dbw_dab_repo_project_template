-- =============================================================================
-- ops.recon  -  Cloudera to Databricks parity evidence
-- =============================================================================
-- THIS IS THE MIGRATION'S EVIDENCE BASE.
--
-- For a lift and shift the client's real question is not "did it deploy?" but
-- "does Databricks produce the same numbers Cloudera did?". These three tables
-- are the answer, and they turn cutover from a judgement call into a query.
--
--   parity_run            one row per reconciliation run per target
--   parity_check_result   one row per check within that run
--   parity_exception      sample mismatching rows, for investigation
--
-- WRITTEN BY the recon bundle only (bundles/recon, owned by QA). The _platform
-- bundle owns the DDL because it is shared infrastructure; the recon service
-- principal is the only identity with MODIFY on this schema.
--
-- A use case is cleared for cutover when its checks pass within tolerance across
-- an agreed number of consecutive runs. See docs/13-migration-and-cutover.md.
--
-- Drop this schema once every use case has cut over and the legacy platform is
-- decommissioned. It is migration scaffolding, not a permanent fixture.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.parity_run (
  recon_run_id      STRING    COMMENT 'Databricks run id of the reconciliation job.',
  use_case          STRING,
  target_name       STRING    COMMENT 'Logical name from conf/reconciliation.yml.',
  layer             STRING    COMMENT 'curated | datamart',
  target_table      STRING    COMMENT 'Fully-qualified Databricks table.',
  source_ref        STRING    COMMENT 'How the legacy side was read: table, view or extract.',
  env               STRING,
  status            STRING    COMMENT 'PASSED | FAILED | SKIPPED. SKIPPED means the ETL had not run, so nothing was compared.',
  checks_total      INT,
  checks_passed     INT,
  checks_failed     INT,
  overall_passed    BOOLEAN   COMMENT 'True only when every check passed within tolerance. False for SKIPPED.',
  started_at        TIMESTAMP,
  ended_at          TIMESTAMP,
  notes             STRING    COMMENT 'Free text: known differences, incident references.'
)
USING DELTA
COMMENT 'One row per reconciled table per run. The cutover evidence trail.'
PARTITIONED BY (env);

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.parity_check_result (
  recon_run_id      STRING,
  use_case          STRING,
  target_name       STRING,
  check_name        STRING,
  check_type        STRING    COMMENT 'row_count | column_sum | column_hash | distinct_count | min_max',
  column_name       STRING,
  legacy_metric     DECIMAL(38,6) COMMENT 'Measurement from the Cloudera side.',
  target_metric     DECIMAL(38,6) COMMENT 'Measurement from the Databricks side.',
  difference        DECIMAL(38,6) COMMENT 'legacy_metric - target_metric.',
  relative_diff     DOUBLE    COMMENT 'abs(difference) / abs(legacy_metric).',
  tolerance         DOUBLE    COMMENT 'Fractional tolerance allowed for this check.',
  justification     STRING    COMMENT 'Why a non-zero tolerance is acceptable. Required when tolerance > 0.',
  passed            BOOLEAN,
  env               STRING,
  evaluated_at      TIMESTAMP
)
USING DELTA
COMMENT 'One row per parity check. Written by each use case''s reconciliation job.'
PARTITIONED BY (env);

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.parity_exception (
  recon_run_id      STRING,
  use_case          STRING,
  target_name       STRING,
  check_name        STRING,
  key_values        MAP<STRING, STRING> COMMENT 'Business key of the mismatching row.',
  legacy_value      STRING,
  target_value      STRING,
  env               STRING,
  captured_at       TIMESTAMP
)
USING DELTA
COMMENT 'Sample mismatching rows. Capped per run - this is for diagnosis, not a full diff.'
PARTITIONED BY (env);

-- -----------------------------------------------------------------------------
-- Cutover readiness. A view rather than a table so it can never drift from the
-- underlying evidence: it is always computed from what actually ran.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{catalog}}.{{schema}}.cutover_readiness
COMMENT 'Consecutive clean reconciliation runs per use case. The cutover gate.'
AS
WITH ranked AS (
  SELECT
    use_case,
    env,
    recon_run_id,
    started_at,
    -- A run is clean only when EVERY target in it passed. One failing table
    -- means the use case is not ready, however good the others look.
    min(CASE WHEN overall_passed THEN 1 ELSE 0 END)   AS run_clean,
    -- A SKIPPED run is NOT clean. That is deliberate: a use case whose ETL keeps
    -- failing never accumulates consecutive passes, so the gate cannot be dodged
    -- by simply not running the pipeline.
    max(CASE WHEN status = 'SKIPPED' THEN 1 ELSE 0 END) AS run_skipped,
    count(*)                                           AS targets_checked
  FROM {{catalog}}.{{schema}}.parity_run
  GROUP BY use_case, env, recon_run_id, started_at
)
SELECT
  use_case,
  env,
  count(*)                              AS total_runs,
  sum(run_clean)                        AS clean_runs,
  sum(run_skipped)                      AS skipped_runs,
  max(started_at)                       AS last_run_at,
  max_by(run_clean, started_at) = 1     AS last_run_clean,
  max_by(run_skipped, started_at) = 1   AS last_run_skipped,
  max_by(targets_checked, started_at)   AS targets_in_last_run
FROM ranked
GROUP BY use_case, env;
