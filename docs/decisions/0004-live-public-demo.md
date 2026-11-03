# 0004 — A public, always-on live demo on a small VPS

Date: 2026-11-03 · Status: accepted (plan of record — execution pending
provider choice) · Trigger: the storefront demo (#163/#164/#165) became the
sales story, but GitHub Pages is static, so the "Try it" section on
product.html hands visitors setup instructions instead of a working demo.

## Context

The ordering storefront drives a real API: quote → coupon → order → pay →
dispatch → delivered, every step auditable (#159 items 1–3). Today it is
fully interactive in exactly two places: the operator's machine (stack
running) and a guided call. On GitHub Pages it renders with an offline
banner by design — static hosting cannot run Postgres, OSRM or the API.

The sales page now promises "see it run before you talk to us". The honest
next step is a small always-on deployment so that promise is clickable for
everyone. This record plans it; 0003 stays scoped to the repo-visibility
question and is unaffected.

## Decision

**1. A "demo" service profile, not the whole stack.** The full compose
stack is 16 containers (Airflow, Kafka, MLflow, dbt, Grafana, …) — that is
the production story, not the demo. A new compose profile (`--profile
demo`) runs only what the storefront touches:

| Service | Why the demo needs it |
|---|---|
| postgres (pgvector) | every table, RLS, coupons, payments |
| api (uvicorn) | the storefront IS this API + serves the page same-origin |
| osrm | road-distance dispatch + route capture (5–8s first order) |
| migrate (one-shot) | applies the Alembic chain on boot |
| relay + worker | drains the outbox so events actually flow (and monitoring stays truthful) |

Excluded from the demo profile: Airflow, MLflow serving loop, dbt, Grafana,
Prometheus, Alertmanager, k6, the dashboard (the storefront page is the
demo surface). Demand forecasts are seeded as rows, so live surge derivation
keeps working without the ML pipeline.

**2. One small VPS, Caddy for TLS, nothing exotic.** Target: ~€5/month box
(Hetzner CX22 ≈ €4.5 — recommend; alternatives: DigitalOcean $6, Hetzner
from a closer region, or Oracle's free ARM tier if availability allows).
Memory budget: the trimmed profile fits in ~2 GB (postgres 1g cap, api 3
workers, OSRM ~500 MB with Tunisia extract). Caddy terminates TLS with
automatic certificates for `demo.<domain>` — or a free DuckDNS subdomain
while no domain exists.

**3. Sandbox posture, stated out loud.** The demo publishes its own
credentials (the storefront page literally says `ops/ops`), and that is the
point: it is a resettable sandbox, not production.

- Keep the published demo trio; RLS and the uniform-422s already hold.
- Nightly reset: a tiny cron re-runs the seed scripts (demo trio, coupon,
  driver availability, fresh forecast rows) and truncates demo orders —
  the demo wakes up clean every morning.
- Admin endpoints (`POST /tenants`) are part of the sandbox; the nightly
  reset bounds any mischief.
- No OpenAI key on the box: the copilot falls back to its documented
  degraded chain (that behavior is itself part of the demo).
- Secrets via the existing `secrets/` file pattern; UFW allows only
  22/80/443.

**4. The sales pages link the LIVE demo once it exists.** product.html's
"Try it" section and index.html's nav get the VPS URL (the storefront
served BY the API — same-origin). The Pages copy stays as the
setup-instructions fallback. An external uptime pinger (UptimeRobot free
tier) watches the demo URL; a "live" badge on the product page flips
manually until it proves itself.

## Phases

1. **A — demo compose profile** (code): new profile in docker-compose.yml,
   trimmed + memory-capped; verify the full storefront flow against the
   trimmed profile locally. ~half a day.
2. **B — provision + deploy** (user + code): user creates the VPS and
   provides SSH access; deploy = install Docker, clone, `.env` + secrets,
   `docker compose --profile demo up -d --build`, Caddy in front. ~half a
   day including TLS.
3. **C — wire the sales pages** (code): links, decision-record status flip
   to "running", README note. ~an hour.
4. **D — hygiene** (code): nightly reset cron on the box, uptime pinger,
   cost review after the first month.

## Consequences

- ~€5/month run cost + a domain if wanted (~€10/year) — the first money the
  project spends on itself; everything before it was free-tier.
- The demo profile becomes the canonical "show someone" configuration — the
  README gains a "run the demo profile" section alongside the full stack.
- The sandbox database is disposable by definition; no real client data
  ever touches this box (that boundary is the whole point of the demo
  tier).
- If the box dies or the bill annoys, the fallback is today's state (Pages
  + offline banner) — nothing else breaks.

## References

- #159 items 1–3 (the storefront, coupons, payments — the thing being hosted)
- 0003 (repo-visibility hosting — separate concern, unaffected)
- #114 (the issue that carries the hosting conversation)
