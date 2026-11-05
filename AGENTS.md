# AGENTS.md — working rules for AI agents in this repository

These are the owner's standing rules, accumulated from real rounds. Every
agent (or human) working in this repo follows them. Violations of these
have each cost a real debugging round — they are not style preferences.

## Issue discipline

1. **Every issue MUST be layered to a milestone at creation time.** An
   issue without a milestone is an orphan — it is invisible on the board's
   version view and will silently rot. If no existing milestone fits,
   create one (a short version-shaped title, e.g. "Fleet health —
   self-reporting pipelines") and assign the issue to it in the same API
   call. The board's history of "shipped-but-untracked work" (#151, #152)
   is why this rule exists.
2. **One commit per issue.** A pull request may carry several commits, but
   each commit maps to exactly one issue (reference its number in the
   message). Never bundle multiple issues into one commit.
3. **Every delivered issue closes with evidence** — the merged commit,
   the live verification output, or the honest deferral and why.

## Verification discipline

4. **Run the full gate before every push:** `python scripts/run_gates.py`
   (ruff check, ruff format --check, the inline-JS page gate, mypy,
   pytest with the coverage floor, pip-audit). It is ONE process with a
   real exit code — never re-implement it as hand-typed pipes (pipes mask
   exit codes; this has bitten three times).
5. **Gates run on the bytes you ship** — after the final edit, not before.
6. **Fleet sweep when the round touches the stack:** `docker compose ps`
   across ALL profiles (profiles hide services) plus one
   `airflow dags list-runs` sweep across every DAG. A reboot silently
   leaves tools-profile services down; scheduled DAGs fail while nobody
   watches.

## Content discipline

7. **Strategy stays local.** Competitive analyses, pricing thinking, and
   client strategy live in `local/` (gitignored) or LEARNING.md — never in
   the public repo. What ships is the resulting code.
8. **Every milestone updates its representations:** README, repo
   description/topics, the landing + product pages, api.md, and LEARNING.md
   — the board, the repo, and the websites must tell the same story.
9. **Never commit on main directly** — branch first, even for "tiny"
   changes. (This has slipped seven times; the recovery is
   `git branch <name> <sha>` at the commit, `git reset --hard origin/main`,
   push the branch.)
10. **Commit messages are accurate and neutral** — they describe what the
    change does, never mention authorship tooling, and strategy never
    leaks into them.

## Posture

11. **Every layer degrades, never dies:** ML down must not break the API;
    a telemetry outage must not break an eval; a memory upgrade must not
    kill the chat. Loud failures belong to ingest/batch paths; request
    paths degrade with a warning.
12. **Verification runs on the real stack** for anything user-facing —
    the TestClient suite plus a live browser/API check of the actual
    flow, not just the widget existing.
