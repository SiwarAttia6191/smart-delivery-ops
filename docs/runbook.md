# Runbook — operational notes and accepted trade-offs (#76)

The operational half of the Phase-3 review's parked findings: what to do
at 3 a.m., and which behaviors are *deliberate*.

## Recovery: wiping the ops database (m4 — incarnation tokens)

Every wire message carries an `incarnation` token (generated once per
ops-database lifetime, stored in `deployment_state`, stamped by the
relay). The warehouse raw layer dedupes on **(incarnation, event_id)** —
so after a wipe-and-rebuild of the ops database, the new incarnation's
events (fresh id sequences, colliding id ranges) are ingested as NEW rows
instead of being silently deduplicated away by `ON CONFLICT DO NOTHING`.

- Rows ingested before this change carry `incarnation = 'legacy'` and
  dedupe exactly as before.
- Two incarnations may legitimately reuse id ranges — that is why the
  dbt `unique` test on bare `event_id` was replaced by singular tests
  asserting the **(incarnation, event_id)** pair.
- No wipe coordination is needed anymore; the token does the separating.
  A full replay across incarnations simply grows the raw layer by both
  histories — which is the truthful record.

## Driver supply: hysteresis and the drift guard (m5)

`location_update` events are emitted only on movement (>500 m from the
last evented position — volume scales with movement, not pings). Two
subtleties are now guarded:

- **The movement baseline** is the last event of any location-bearing
  type (availability transitions carry positions and reset it — cheap).
- **The cumulative-drift guard**: availability churn resets that baseline
  repeatedly, which could mask endless sub-threshold drift. A second
  threshold (`DRIVER_DRIFT_EMISSION_THRESHOLD_M`, default 2000 m)
  measures from the last *real* `location_update` and trips regardless of
  availability resets. Net effect: no driver can drift arbitrarily far
  without the event stream noticing.

## Supply overcount while the worker is down (monitoring note)

Open supply intervals run to "now" until a sweep closes them. While the
relay/sweep worker is down, the supply mart (rebuilt daily) can
overcount available drivers; the catch-up sweep plus the next rebuild
self-heal. Watch `sdo_outbox_unpublished` and
`sdo_outbox_last_relay_age_seconds` — a growing backlog or stale age is
the signal the worker is down, before the mart is wrong.

## route_cache growth (m7)

`route_cache` holds one row per distinct quoted (pickup, dropoff) pair at
full client precision — bounded by real quoting patterns in practice,
unbounded in principle. `sdo_route_cache_rows` on `/analytics/metrics`
makes the size visible. Prune policy when it ever matters: delete rows
untouched for N days (`cache_hits == 0` and stale `updated_at`), run
off-peak; the cache is a pure optimization and safe to truncate.

## Zone-cell rounding semantics (accepted trade-off)

SQL `round()` (half-away-from-zero) vs pandas `round()` (half-even) can
split boundary coordinates across cells between the API features and the
warehouse marts. The failure mode is a *safe neutral* demand ratio for
the affected zone-hour (the forecast anchors to observed history, and the
surge tiers are coarse). Accepted: not worth forcing one rounding mode
across two engines for boundary-only skew.

## ETA model reload (hot reload)

The serving path re-checks the registry's promoted Production version at
most once per `ETA_MODEL_RELOAD_TTL_SECONDS` (default 60 s) and swaps the
booster in place when it changed — no API restart on promotion. Fail-open
in both directions: an unreachable registry keeps the currently loaded
model answering (log line, no crash), and a failed reload falls back to
the heuristic exactly like a boot-time failure.

## Memory budget (compose)

Services run with explicit `mem_limit`s (postgres 1g, kafka 768m,
airflow-scheduler 1g, osrm 1g, api 640m, mlflow/airflow-web 640m, ingest
512m, migrate/dbt 512m, worker 384m, kafka-init 128m) — the stack is
meant to fit in ~7 GB. If a service is OOM-killed, check the limit
against actual usage before raising it; the limits exist to fail loudly
rather than starve the host.

## dbt output schemas: production vs draft (#54)

Builds from `main` write the production marts into `analytics` (and
`staging`); builds from any other branch land in `analytics_draft` /
`staging_draft`. The rule is enforced in two places: profiles.yml defaults
`DBT_SCHEMA` to `analytics_draft` (an un-configured build can never touch
production), and `make dbt` maps the checked-out branch (main → analytics,
anything else → draft). The daily Airflow DAG pins `DBT_SCHEMA=analytics`
explicitly — it IS the production build.

- **Which schema did a build use?** `make dbt` prints it; from hand-rolled
  compose runs, the env you passed decides: `DBT_SCHEMA=analytics docker
  compose run --rm dbt build`.
- **Clear the draft without touching production:**
  `DROP SCHEMA analytics_draft CASCADE; DROP SCHEMA staging_draft CASCADE;`
  — production objects live in different schemas and are unreachable by
  these drops.
- **Feature branches share one draft schema** by design: a draft build
  replaces the previous branch's draft. That's the point — the guarantee
  is that non-main builds never modify `analytics`.
- The dashboards, ML feature feed and benchmarks read `analytics` only;
  a draft build is invisible to everything that matters.
