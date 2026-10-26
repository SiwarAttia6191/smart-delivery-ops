# Contributing

The rules that keep this repo's history clean and its pipeline trustworthy.
They apply to humans **and** coding agents (Copilot included) alike — the
process gaps this project has hit were all agent-driven, so the conventions
are written down, not assumed.

## Branch and PR discipline

1. **Branch first, always.** `git checkout -b <type>/<task>` *before* the
   first commit. Never commit on a local `main`: the branch ruleset guards
   the *remote* push, but a local-main commit creates recovery work (branch +
   reset + PR) every time it happens.
2. **One task = one branch = one PR.** Open the PR immediately after the
   push — a pushed branch without a PR is invisible to review and to `ci-ok`.
   The README travels with the change: every PR that alters behavior,
   configuration, or the pipeline updates `README.md` (and `data/README.md`
   where relevant) **in the same PR** — documentation is never a follow-up.
3. **No merge without `ci-ok`.** The ruleset enforces it; do not look for
   workarounds. Rebase-merge multi-commit PRs so per-task commits survive;
   squash single-commit ones.
4. **Conventional Commits** style, imperative mood, no AI attribution
   trailers or badges.

## Verification before every push

5. **Run the gates with explicit exit codes**: `pytest`, `mypy apps core
   domain scripts tests`, `ruff check .`, `ruff format --check .`. Never
   `cmd | tail -1` — a pipeline's exit status is the *last* command's, and a
   failed build has looked green that way (it cost main a broken day).
6. **dbt changes require an explicit-exit-code `dbt build`** before the PR
   (`docker compose run --rm dbt build; echo $?`). `dbt parse` is not enough —
   it cannot catch a renamed column.
7. **Worker changes with transaction semantics require a Postgres-path
   check.** SQLite unit tests do not poison transactions and do not enforce
   `FOR UPDATE SKIP LOCKED` — both masked real bugs before.
8. **Live-verify the user-visible behavior** on the compose stack when the
   change touches it (an endpoint, a worker loop, a DAG run): demonstrated
   behavior, not just green checks.

## Data and pipeline conventions

9. `data/benchmarks/` reports are committed evidence — always embed the
   dataset snapshot (`max(event_id)` + git SHA).
10. Business-time grains (day, hour) come from staging's
    `source_created_at_local` (Africa/Tunis); the warehouse stores UTC.
11. Map data (`data/osrm/`) is gitignored and regenerable — see
    `data/README.md`.

## Housekeeping

- Delete feature branches (local and remote) after merge; stale branches are
  clutter and mask the real state.
- The repo description and topics list **only what is built** — update them
  at each milestone, never ahead of it.
