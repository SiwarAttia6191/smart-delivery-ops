# Coding-agent instructions

Before writing any code in this repository, read [CONTRIBUTING.md](../CONTRIBUTING.md)
and follow it exactly. The short version:

- Branch first (`git checkout -b <type>/<task>`); never commit on local main.
- One task = one branch = one PR, opened immediately after the push.
- Gates before every push with explicit exit codes: pytest, mypy
  (apps core domain scripts tests), ruff check, ruff format --check.
- dbt changes require an explicit-exit-code `dbt build` before the PR.
- Worker changes with transaction semantics require a Postgres-path check.
- Update README.md in the same PR when behavior, config, or the pipeline changes.
- Conventional Commits; no AI attribution trailers.
