# Contributing

The short version. Details in [docs/](docs/00-START-HERE.md).

---

## Before your first change

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e libs/dab_common
databricks auth login --host https://adb-0000000000000001.1.azuredatabricks.net
```

Full setup: [docs/03 — Developer guide](docs/03-developer-guide.md).

---

## The loop

```bash
git checkout main && git pull
git checkout -b feature/DAB-123-short-description

# work, and deploy to your own sandbox as often as you like
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle landing

# before you push - same four checks as the PR build
pwsh ./scripts/dev/Validate-All.ps1

git push -u origin feature/DAB-123-short-description
```

Then open a PR into `main`.

---

## Rules

**Branch names carry a ticket ID.** `feature/DAB-123-onboard-invoices`. The branch
policy rejects a PR with no linked work item.

**Never commit a secret, a workspace URL or a token.** The cross-reference audit
fails the build if it finds one, but do not rely on it. If a secret is ever
committed, **rotate it** — removing the commit is not enough.

**Environment-specific values are variables.** If a value differs between dev and
prod it belongs in `variables.yml` and the target blocks — never hardcoded in
`resources/` or a `.sql` file. There is a test that fails if a SQL file names a
catalog.

**Logic goes in the wheel, orchestration goes in the notebook.** A notebook cannot be
unit tested. If an entry notebook grows past ~50 lines of real logic, move it into
`src/<module>_module/` and give it a test.

**Shared schemas belong to `_platform`.** Do not add `resources.schemas` to a module
bundle — it breaks every sandbox deploy. See
[docs/08](docs/08-troubleshooting.md#schema-prefix).

**A change under `libs/` affects all three modules.** Say in the PR description that
you checked them.

**Onboarding a source is a YAML row**, not a notebook. Edit
`bundles/landing/conf/*_sources.yml` and run
`pytest bundles/landing/tests/test_seed_files.py`.

---

## Pull requests

- Squash merge; the PR title becomes the commit on `main`, so write it properly:
  `DAB-123: onboard SALES.INVOICES for us2`
- Two approvals, one of which must be a lead. You cannot approve your own.
- Votes reset when you push, so get the build green first.
- All comments must be resolved.

Review checklist: [docs/04 §9](docs/04-bundle-authoring.md#9-review-checklist).

---

## Fixing something found in preprod or prod

**On the release branch, not `main`, and never in the workspace.**

```bash
git checkout release/2026.09.1 && git pull
git checkout -b bugfix/DAB-456-short-description
# fix, test, validate
```

PR into `release/2026.09.1`. Then **back-merge to `main`** — this is mandatory, and
the release is not closed without it.

Full flow: [docs/02 §5–6](docs/02-branching-strategy.md#5-fixing-a-bug-found-in-preprod).

---

## Adding a use case

```bash
databricks bundle init ./templates/use-case-bundle --output-dir bundles
```

Then: [docs/04 §8](docs/04-bundle-authoring.md#8-adding-a-whole-new-module). The
cross-reference audit fails until you have added a CD pipeline — that is deliberate.

---

## Cleaning up

When your feature is merged:

```bash
pwsh ./scripts/dev/Destroy-Sandbox.ps1 -Bundle landing
```

It leaves your schemas alone. Drop them when you are sure:

```sql
DROP SCHEMA IF EXISTS edp_curated_nonprod.<you>_us1 CASCADE;
DROP SCHEMA IF EXISTS edp_landing_nonprod.<you>_us1 CASCADE;
DROP SCHEMA IF EXISTS edp_ops_nonprod.<you>_audit CASCADE;
```

Make it part of closing the ticket. Ten developers who never clean up leave a
workspace nobody can navigate.
