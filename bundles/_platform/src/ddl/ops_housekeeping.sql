-- =============================================================================
-- ops.audit  -  sandbox housekeeping views
-- =============================================================================
-- Sandbox schemas are created at RUNTIME by ensure_schema(), so no bundle owns
-- them and `bundle destroy` cannot remove them. One developer across five use
-- cases accumulates 19 schemas; ten developers who never clean up leave ~190 and
-- the metastore stops being navigable.
--
-- These views make the sprawl visible so leads can chase it, and generate the
-- DROP statements rather than executing them. Nothing here drops anything:
-- automatically deleting a developer's work-in-progress is the kind of helpful
-- that costs somebody a day.
--
-- {{catalog}} and {{schema}} are substituted by bootstrap_ops_framework.py.
-- =============================================================================

-- Every sandbox schema in the metastore, with how stale it is.
-- A sandbox schema is one whose name is <something>_<use_case> or
-- <something>_<ops schema> - i.e. it carries a prefix the platform did not
-- declare. The shared schemas are exactly the undecorated names.
CREATE OR REPLACE VIEW {{catalog}}.{{schema}}.sandbox_schemas
COMMENT 'Developer sandbox schemas across all catalogs, oldest activity first.'
AS
SELECT
  t.table_catalog,
  t.table_schema,
  -- Everything before the final underscore-delimited segment.
  regexp_extract(t.table_schema, '^(.*)_(us[0-9]+|audit|config|logs|recon)$', 1) AS owner_prefix,
  regexp_extract(t.table_schema, '^(.*)_(us[0-9]+|audit|config|logs|recon)$', 2) AS scope,
  count(*)                                                     AS table_count,
  max(t.last_altered)                                          AS last_touched,
  datediff(current_date(), CAST(max(t.last_altered) AS DATE))  AS days_idle
FROM system.information_schema.tables AS t
WHERE t.table_catalog LIKE '%\\_nonprod' ESCAPE '\\'
  -- Shared schemas are the bare names; anything with a prefix is a sandbox.
  AND t.table_schema RLIKE '^.+_(us[0-9]+|audit|config|logs|recon)$'
GROUP BY t.table_catalog, t.table_schema;

-- Sandbox schemas nobody has touched in 30 days. The chase list.
CREATE OR REPLACE VIEW {{catalog}}.{{schema}}.stale_sandbox_schemas
COMMENT 'Sandbox schemas idle 30+ days, with the DROP statement ready to review.'
AS
SELECT
  owner_prefix,
  table_catalog,
  table_schema,
  table_count,
  last_touched,
  days_idle,
  -- Generated, NOT executed. A human decides.
  concat('DROP SCHEMA IF EXISTS ', table_catalog, '.', table_schema, ' CASCADE;')
    AS drop_statement
FROM {{catalog}}.{{schema}}.sandbox_schemas
WHERE days_idle >= 30
ORDER BY days_idle DESC;

-- Per-developer totals. What a lead looks at in a weekly tidy-up.
CREATE OR REPLACE VIEW {{catalog}}.{{schema}}.sandbox_footprint
COMMENT 'Sandbox schema and table counts per developer.'
AS
SELECT
  owner_prefix,
  count(DISTINCT concat(table_catalog, '.', table_schema)) AS schemas,
  sum(table_count)                                         AS tables,
  min(days_idle)                                           AS days_since_last_activity
FROM {{catalog}}.{{schema}}.sandbox_schemas
GROUP BY owner_prefix
ORDER BY schemas DESC;
