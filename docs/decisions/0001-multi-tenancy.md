# Decision record 0001 — multi-tenancy model

**Status:** accepted (2026-10-31, issue #36)
**Context:** issues #35 (tenancy foundation) and #36 (isolation layer)

## Decision

**Shared schema + `tenant_id` column + Postgres Row-Level Security** is the
tenancy model. Every business row carries `tenant_id`; the database engine
— not application WHERE clauses — enforces that a request can only touch
rows of the tenant its JWT belongs to.

## Why this model

| model | fits | costs |
|---|---|---|
| shared schema + RLS **(chosen)** | many small tenants, one deployment, one migration chain, one pool | isolation depends on the policies being correct; noisy neighbors share hardware |
| schema-per-tenant | mid-size tenants needing custom columns/backups | every migration runs N times; connection pools multiply; cross-tenant analytics needs a federation layer |
| database-per-tenant | compliance-heavy enterprise (data residency, hard guarantees) | infra cost per tenant; deployments become a fleet; cross-tenant product features get very hard |

The standard SaaS path is to START shared and graduate per-customer — the
chosen model defers that decision without foreclosing it: `tenant_id` is
already the partition key, so a tenant can be extracted to its own schema
or database by replaying the warehouse (which already carries the tenant
in the event envelope) or by row-level export.

## How isolation actually works

1. `users.tenant_id` binds every identity to one tenant; the JWT's `tid`
   claim is issued FROM that column, so a token can never claim a tenant
   its user doesn't belong to.
2. The ASGI middleware pins `tid` into a request-scoped context.
3. A SQLAlchemy session listener pins the `app.current_tenant` GUC at the
   start of every transaction (`SET LOCAL` — dies with the transaction, so
   pooled connections never leak one request's tenant into the next).
4. Forced RLS policies on the six business tables compare every row's
   `tenant_id` against that GUC — for SELECT, UPDATE, DELETE (USING) and
   INSERT (WITH CHECK).
5. Anonymous traffic (the documented demo posture: open reads) runs in the
   default tenant's scope — the demo data IS tenant 1.

## Two findings this decision records

- **A superuser bypasses RLS by definition.** The bootstrap
  `POSTGRES_USER` role is a superuser; running the API as it would make
  the policies decorative. The runtime services (api, worker) therefore
  connect as `sdo_app` — a least-privilege, non-superuser role the
  policies apply to. The admin role stays for migrations (DDL). The
  data-plane smoke proves enforcement under a least-privilege role for
  exactly this reason.
- **Internal services see across tenants through an explicit GUC.** The
  relay publishes every tenant's outbox and the benchmarks evaluate the
  whole history; they set `app.tenant_bypass = '1'` per engine — an
  explicit, grep-visible cross-tenant declaration, the opposite of a
  forgotten WHERE clause.

## Per-tenant configuration

`tenants.pricing_overrides` (JSONB) holds per-tenant fee/surge knobs,
whitelisted to the pricing settings and merged over the deployment
defaults at quote/order time (`GET/PUT /pricing/config`). The Phase 4
dashboard's tenant filter consumes `GET /analytics/by-tenant`, which
computes each tenant's slice in its own RLS scope.

## When we would graduate

- a tenant demands **hard physical separation** (regulatory or
  contractual) → database-per-tenant for that tenant, bridge via the
  warehouse;
- a tenant needs **custom columns or independent migration cadence** →
  schema-per-tenant (the migration chain gains a per-tenant loop);
- a tenant's volume **degrades neighbors** (noisy neighbor) → dedicated
  deployment first (the region pack already makes one-deployment-per-city
  a manifest change), extraction second.
