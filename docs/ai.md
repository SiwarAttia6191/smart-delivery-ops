# The AI layer — what is built, how it works, what comes next

The platform's AI is not a chat bolted onto a database. It is an
**agent over the governed API**: a LangGraph supervisor whose tools ARE
the platform's own read logic, executed with the caller's session so
Postgres Row-Level Security scopes every answer to the caller's tenant.
This document is the honest map: what shipped, how each piece works,
and what could come next (with the deliberate non-goals).

**Phase issues:** [#120](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/120)
core + RAG + tracing · [#121](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/121)
live chain + budgets · [#122](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/122)
incident narrator · [#123](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/123)
eval harness · [#124](https://github.com/SiwarAttia6191/smart-delivery-ops/issues/124)
the LLM-observability deferral record.

## What is shipped (all four phases)

| Phase | What shipped | Code | Evidence |
|---|---|---|---|
| A — the agent | LangGraph supervisor, 5-tool read-only registry, guardrails, scripted provider proving the whole loop keyless in CI, `POST /copilot/chat` | `apps/copilot/graph.py`, `apps/copilot/tools.py`, `apps/copilot/guardrails.py`, `apps/api/routes/copilot.py` | 33+ unit tests; injection probes answer 422 |
| A — RAG | pgvector knowledge store (VECTOR 1536), heading-aware chunker, deterministic hash embedder, `search_docs` as the fifth tool — plus the **Obsidian bridge**: personal vault notes join the corpus (`obsidian:` namespaced, `source_prefix` scoping) | `apps/copilot/retriever.py`, `scripts/ingest_docs.py`, `scripts/ingest_obsidian.py`, migration 005 | 16 chunks live from the runbook + decision records; a starter vault ingested and cited by the agent |
| A — tracing | One MLflow run per conversation: provider, rung, tool-call count, degraded reason, token total, full message log artifact | `apps/copilot/tracing.py` | the `copilot` MLflow experiment |
| B — live chain | Flagship→mini→canned fallback chain; per-rung reporting (`provider=openai:<model>`); `degraded_reason` names every failure in the payload; tool + token budgets (`copilot_max_tokens_per_chat`) | `apps/copilot/graph.py`, `apps/copilot/providers/` | live-verified with a real 429: the reason lands in the API response |
| C — narrator | Alert-path narration: the failure alert carries a Slack-ready summary correlated with the platform's metrics + the latest MLflow benchmark run; fails open per source | `apps/copilot/narrator.py`, `dags/ml_alerts.py` | in-container narration with real numbers; 9 offline tests |
| D — evals | A versioned golden set (question → expected tools → answer shape) enforced in CI; a scheduled Airflow eval scoring the configured provider into MLflow | `apps/copilot/evals.py`, `tests/eval/`, `ml/evals/run_copilot_eval.py`, `dags/copilot_evals.py` | 7/7 = 1.00 live run; the `copilot_evals` experiment |

## The reliability layers (why each exists)

- **Tenant scoping by construction** — the graph's tools run on the
  request's session; RLS does the isolation, not a prompt.
- **Guardrails first** — injection-pattern screening and a 2,000-char
  cap reject before any model call.
- **Budgets, not vibes** — `MAX_TOOL_CALLS` (4) ends runaway loops via a
  `budget_stop` node; the vendor-reported token usage ends overspends
  via `token_stop` (default 40k/chat). LangGraph's recursion limit
  backs both.
- **A measured fallback chain** — flagship model, then mini, then a
  canned answer; `degraded_reason` carries each failure into the
  payload so on-call can triage without logs. A provider outage
  degrades the answer, never the endpoint (200 with `degraded: true`).
- **Fail-open by contract** — tracing, narration, and eval logging all
  survive their remotes being down; kill switches: `SDO_TRACING`,
  `SDO_NARRATOR`.
- **Evals that can fail** — the harness carries its own can-fail tests
  (a wrong tool choice and a shallow answer both score FAIL), so it
  can never become a rubber stamp.

## Running it

```bash
docker compose up -d                       # the stack
python scripts/create_user.py --demo       # demo users: ops/ops, admin/admin
TOKEN=$(curl -s -X POST localhost:8010/auth/token -d 'username=ops&password=ops' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST localhost:8010/copilot/chat -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"How many orders were delivered this week?"}'
```

- **Provider switch**: `COPILOT_PROVIDER=scripted` (default — deterministic,
  CI-safe) or `openai` + a funded `OPENAI_API_KEY` secret. The live chain and
  the prompt version ride the same settings (`COPILOT_MODEL`,
  `COPILOT_MODEL_MINI`).
- **RAG corpus re-ingest**: `python scripts/ingest_docs.py --reset` (chunks
  `docs/runbook.md` + `docs/decisions/*.md` into pgvector).
- **Your Obsidian vault as knowledge**: `python scripts/ingest_obsidian.py`
  ingests a vault (default `~/Documents/SmartDeliveryVault`, `--vault` to point
  elsewhere) into the same store, namespaced `obsidian:<note path>`. The
  search_docs tool takes an optional `source_prefix` — "obsidian:" scopes a
  question to YOUR notes ("what did I note about deploys?"), "docs:" to the
  shipped documentation. Honest caveat: the hash embedder compresses scores,
  so cross-corpus ranking is weak until Phase B's real embeddings land —
  scoping is the fix that works today.
- **Evals**: `pytest tests/eval/` (CI gate) or
  `python ml/evals/run_copilot_eval.py --min-pass-rate 0.8` (logs one MLflow
  run per batch; the `copilot_evals` DAG schedules it daily, paused by
  default).
- **Narrator drill**: `dags/ml_alerts.py` narrates every Airflow failure;
  `SDO_NARRATOR=0` disables.

## What could come next (honest backlog, not a promise)

Ordered by value-to-effort, each mapped to the layer it strengthens:

1. **Live-model serving** (B finish line) — the account key is wired; the
   moment OpenAI credits exist, `COPILOT_PROVIDER=openai` serves the
   flagship and the scheduled eval starts measuring it. Zero code.
2. **Confirm-before-write / HITL** (#121's remaining checkbox) — the tool
   registry is read-only today; write actions (reassign an order, pause a
   driver) arrive with a LangGraph interrupt: the agent proposes, a human
   confirms, the graph resumes.
3. **Streaming answers** — the endpoint is synchronous; SSE streaming of
   the supervisor's turns would make the copilot feel instant. Requires
   moving the request-scoped session binding to a context variable
   (documented in `graph.py`).
4. **Conversation memory** — every question is stateless today; a session
   store (the `conversations` table + last-k turns in the prompt) enables
   follow-up questions.
5. **LangSmith evaluation** — the stack decision (#120) named LangSmith
   the industry tool to learn: mirror the golden-set eval there as the
   secondary sink, keeping MLflow as the default (sink by config).
6. **Real embeddings + reranking** — swap the hash embedder for a real
   embedding model (the interface is ready), which fixes the compressed
   cross-corpus scores; a cross-encoder reranker over the pgvector top-k
   is the growth path when the corpus grows past a few hundred chunks.
7. **Advanced RAG (reranking)** — the Naive-in-a-tool tier is documented
   in LEARNING §38b; a cross-encoder reranker over the pgvector top-k is
   the growth path when the corpus grows past a few hundred chunks.
8. **Per-tenant knowledge** — `knowledge_chunks` is deliberately
   deployment-scoped; tenant-specific runbooks arrive with the first
   multi-tenant customer (a `tenant_id` column + RLS on the corpus).

**Deliberately NOT planned** (recorded decisions, not oversights):
agentic retrieval (the corpus is small and curated; a router suffices),
autonomous write actions without confirmation, and any LLM in the
serving path for pricing or dispatch (deterministic domain rules stay
champion; the copilot explains, it does not decide).

## Where the evidence lives

- MLflow UI (`:5100`): the `copilot` experiment (one run per
  conversation) and `copilot_evals` (one run per eval batch).
- LEARNING.md §37–§38j: the design story, the stack decision, every
  gotcha the live rounds forced (restart-is-not-deploy, the pgvector
  casts, the provider-cursor bug), and the interview sound-bites.
- The tests: `tests/unit/test_copilot*.py`, `tests/unit/test_narrator.py`,
  `tests/eval/test_copilot_evals.py` — the whole agent path provable
  keyless.
