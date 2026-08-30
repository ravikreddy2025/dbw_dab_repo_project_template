# `ported/` — lift-and-shift landing zone

Cloudera landing code lands here **near as-is** and runs as notebook or script
tasks. It does not have to be an importable package, and it does not have to have
tests, to be deployed.

That is deliberate. Demanding a full refactor before anything can run is how a
migration stalls: teams under deadline route around the structure instead.

## The path out

| Stage | Where the code lives | Runs as | Tested |
|---|---|---|---|
| 1. Ported | `src/ported/` | notebook / script task | no |
| 2. Wrapped | `src/ported/`, called from a thin entry point | notebook task | smoke only |
| 3. Refactored | `libs/edp_landing/` or `src/landing_module/` | wheel on the job | unit tests |

Stage 3 is the destination, not stage 1. See
[docs/14-porting-guide.md](../../../../docs/14-porting-guide.md).

## Rules while code is here

- **It still gets reviewed.** Ported does not mean unreviewed.
- **No hardcoded catalogs, schemas or hosts.** Even at stage 1, environment
  values come from job parameters via `dab_common.config.build_context()`. This is
  the one refactor that is not optional, because without it the code cannot be
  promoted between environments at all.
- **No credentials.** Cloudera code frequently carries them inline. Move them to a
  secret scope before the first commit, and rotate anything that was ever in git.
- **Record where it came from.** Put the source repo and commit in a header
  comment, so the original is findable when behaviour is questioned.

## Exit criteria

Code leaves `ported/` when it has a reconciliation record showing parity
([docs/13](../../../../docs/13-migration-and-cutover.md)) and a refactor ticket.
Track what is still here:

```bash
find bundles/*/src/ported -name "*.py" | wc -l
```
