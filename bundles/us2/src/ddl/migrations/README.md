# `us2` schema migrations

>>> PLACEHOLDER FOLDER — the structure is the deliverable, not the SQL. <<<

## What goes here

Ordered, once-only changes to tables that **already exist** in an environment.

`../curated/customers.sql` and `../datamart/marts.sql` describe the *current
shape* with `CREATE TABLE IF NOT EXISTS`. They build an empty environment and do
nothing to a populated one. That is the whole problem: the day you add a column,
a fresh sandbox gets it and preprod does not.

## Start here

`EXAMPLE__add_column.sql.example` is a complete, annotated migration. It ends
in `.example`, so neither the job (`V*.sql`) nor the PR audit sees it. Copy it
and rename:

```bash
cp EXAMPLE__add_column.sql.example V002__add_currency.sql
```

**There is no V001.** The initial table shape lives in `../curated/`, so the
first migration is whatever the first *change* turns out to be. An empty
folder is the correct state for a use case nobody has changed yet.

## Naming

```
V007__add_customers_currency.sql
 |      |
 |      lower_snake_case description — ends up in ops.config.schema_migration
 zero-padded ordering number, unique within this use case
```

`us2_migrate` refuses to run on a bad name, a duplicate version, a
migration arriving out of order, or an applied file that was edited afterwards.

## The rules

| | |
|---|---|
| ✅ | One logical change per file. |
| ✅ | Make it idempotent — `ADD COLUMN IF NOT EXISTS`, `CREATE OR REPLACE VIEW`. |
| ✅ | Update `../curated/customers.sql` in the **same PR**, so the shape file stays the truth for a fresh environment. |
| ❌ | Never edit a file after it has merged. Checksum drift fails the job. Fix forward. |
| ❌ | Never renumber a file that has run anywhere. |
| ❌ | No `DROP COLUMN` on a table anything reads. Deprecate, migrate readers, drop a release later. |

## In your sandbox you do not need any of this

A sandbox schema is yours and disposable. Drop the table and rerun:

```sql
DROP TABLE IF EXISTS edp_curated_nonprod.jsmith_us2.customers;
```

Write the migration when the change is going somewhere that already has the
table — which means when you open the PR, not while you are iterating.

## Changing a column type

Delta will not rewrite a column type in place. `ALTER COLUMN ... TYPE` only
widens (`INT`→`BIGINT`, `FLOAT`→`DOUBLE`). Anything else is four steps, usually
across two releases:

```sql
-- V008__add_customers_amount_decimal.sql   (release N)
ALTER TABLE ... ADD COLUMN IF NOT EXISTS amount_v2 DECIMAL(18,2);
UPDATE ... SET amount_v2 = CAST(amount AS DECIMAL(18,2)) WHERE amount_v2 IS NULL;

-- ... readers move to amount_v2, ship, confirm ...

-- V011__drop_customers_amount_double.sql   (release N+1)
ALTER TABLE ... DROP COLUMN amount;
ALTER TABLE ... RENAME COLUMN amount_v2 TO amount;
```

`DROP`/`RENAME COLUMN` need column mapping on the table:

```sql
ALTER TABLE ... SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name');
```

The one-step alternative — rewriting the table with `overwriteSchema` — is fine
for a table nothing depends on yet, and a bad idea for anything a mart reads.

See [docs/04 — Changing an existing table](../../../../../docs/04-bundle-authoring.md).
