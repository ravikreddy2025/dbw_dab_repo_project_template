-- =============================================================================
-- ops.logs  -  structured application logs
-- =============================================================================
-- Distinct from ops.audit on purpose. Audit answers "did it work?" and is
-- queried by QA, support and the release process. Logs answer "what happened
-- inside?" - high-volume, developer-facing, and safe to expire.
--
-- Keep retention short. A logs table nobody prunes becomes the largest and
-- least useful object in the metastore.
-- =============================================================================

CREATE TABLE IF NOT EXISTS {{catalog}}.{{schema}}.application_log (
  run_id       STRING,
  use_case     STRING,
  layer        STRING,
  task_key     STRING,
  env          STRING,
  level        STRING    COMMENT 'DEBUG | INFO | WARN | ERROR',
  logger       STRING    COMMENT 'Module emitting the record.',
  message      STRING,
  context      MAP<STRING, STRING>,
  logged_at    TIMESTAMP
)
USING DELTA
COMMENT 'Structured application logs. Retention enforced by table properties, not by hand.'
PARTITIONED BY (env)
TBLPROPERTIES (
  -- 30 days is plenty for debugging a failed run. Beyond that, ops.audit is the
  -- record of what happened.
  delta.deletedFileRetentionDuration = 'interval 30 days',
  delta.logRetentionDuration         = 'interval 30 days'
);
