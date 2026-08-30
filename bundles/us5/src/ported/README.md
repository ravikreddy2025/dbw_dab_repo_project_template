# `ported/` — us5 lift-and-shift zone

Cloudera code for us5 lands here **near as-is** and runs as notebook or script
tasks. It does not have to be an importable package, and it does not have to have
unit tests, to be deployed.

That is deliberate. Demanding a full refactor before anything can run is how a
migration stalls: teams under deadline route around the structure instead.

## The path out

| Stage | Where the code lives | Runs as | Tested |
|---|---|---|---|
| 1. Ported | `src/ported/` | notebook / script task | no |
| 2. Wrapped | `src/ported/`, called from `src/jobs/` | notebook task | smoke only |
| 3. Refactored | `src/us5_module/` | wheel on the job | unit tests |

Stage 3 is the destination, not stage 1. Full guidance:
[docs/14-porting-guide.md](../../../../docs/14-porting-guide.md).

## Sub-use-cases

Cloudera repos are split by use case *and* sub-use-case. Mirror that here:

```
src/ported/
  billing/          <- sub-use-case
    load_invoices.py
  claims/
    settle_claims.py
```

and when code reaches stage 3, keep the same split:

```
src/us5_module/
  billing/
  claims/
```

Sub-use-cases do **not** get their own schema — everything for us5 lands in
`edp_curated_<env>.us5`. If two sub-use-cases need separate tables, that is a
table-name prefix (`billing_invoice`, `claims_settlement`), not a new schema.

## Rules while code is here

- **It still gets reviewed.** Ported does not mean unreviewed.
- **No hardcoded catalogs, schemas or hosts.** Even at stage 1, environment
  values come from job parameters via `dab_common.config.build_context()`. This is
  the one refactor that is not optional — without it the code cannot be promoted
  between environments at all.
- **No credentials.** Cloudera code frequently carries them inline. Move them to a
  secret scope before the first commit, and rotate anything that was ever in git.
- **Record where it came from.** Put the source repo and commit in a header
  comment, so the original is findable when behaviour is questioned.

## Exit criteria

Code leaves `ported/` when it has a reconciliation record showing parity
([docs/13](../../../../docs/13-migration-and-cutover.md)) and a refactor ticket.
