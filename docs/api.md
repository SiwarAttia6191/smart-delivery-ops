# API Reference

Interactive versions: **/docs** (Swagger UI) and **/redoc** when the API is running.

## Authentication (#28)

Every **mutating** endpoint requires a bearer token; GETs and
`POST /pricing/quote` (a pure computation) stay open — the demo posture.

```bash
# OAuth2 password flow (Swagger UI: the green Authorize button)
curl -X POST localhost:8010/auth/token -d "username=ops&password=ops"
# → {"access_token": "...", "token_type": "bearer"}

curl -X POST localhost:8010/orders -H "Authorization: Bearer $TOKEN" ...
```

| role | can | token lifetime |
|---|---|---|
| `viewer` | reads only (writes → 403) | 12 h |
| `ops` | read + create/advance orders, drivers, customers, restaurants | 12 h |
| `admin` | everything `ops` can (user management arrives with tenancy) | 12 h |

Tokens are HS256 JWTs signed with `AUTH_JWT_SECRET` (default is
DEV-ONLY — see `.env.example`). Demo users: `python scripts/create_user.py --demo`
(`admin/admin`, `ops/ops`, `view/view` — dev-only, matching the Airflow
UI's dev credentials). Unknown user and wrong password return the same
401, so the endpoint can't be used to enumerate accounts.

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Service + database status, version, environment |

## Customers

| Method | Path | Description |
|---|---|---|
| POST | `/customers` | Create a geolocated customer |
| GET | `/customers` | List customers |
| GET | `/customers/{id}` | Get one customer |

## Restaurants

| Method | Path | Description |
|---|---|---|
| POST | `/restaurants` | Create a restaurant (pickup point) |
| GET | `/restaurants` | List restaurants |
| GET | `/restaurants/{id}` | Get one restaurant |

## Drivers

| Method | Path | Description |
|---|---|---|
| POST | `/drivers` | Register a driver (bicycle / motorcycle / car) |
| GET | `/drivers?available=` | List drivers, optionally filtered by availability |
| GET | `/drivers/{id}` | Get one driver |
| PATCH | `/drivers/{id}` | Location + availability heartbeat |

## Orders

| Method | Path | Description |
|---|---|---|
| POST | `/orders` | Create order (quotes fee + ETA, logs `created` event) |
| GET | `/orders?status=&limit=` | List orders (newest first) |
| GET | `/orders/{id}` | Get order with full event history |
| POST | `/orders/{id}/assign?vehicle=` | Dispatch nearest available driver |
| POST | `/orders/{id}/status` | Transition: `in_transit`, `delivered`, `cancelled` |

## Pricing

| Method | Path | Description |
|---|---|---|
| POST | `/pricing/quote` | Fee breakdown for a route (`restaurant_id` **or** pickup lat/lon) |

`demand_ratio` on order creation and quotes is optional: omit it and the API derives it live from the `demand_forecasts` table (observed vs forecast demand for the dropoff zone-hour, floored 0.5 / capped 2.5, neutral 1.0 when no forecast covers the hour).

**Dropoff by address (#232):** both `POST /pricing/quote` and `POST /orders` accept `dropoff_address` **XOR** `dropoff_latitude/longitude` — a typed address resolves through Nominatim (OpenStreetMap's free geocoder) and is cached forever in `geocode_cache` (one row per normalized address, shared across tenants — coordinates are city facts, not tenant data). The quote echoes `dropoff_resolved` (what the text matched) and the order's `created` event carries the audit line (`dropoff_address`, `dropoff_resolved`). Failure shapes: an unresolvable address is **422** `address_unresolvable` (the caller's data problem — a wrong-coordinate delivery is never acceptable); an unreachable provider is **503** `geocoder_unavailable` (retry, or send coordinates). Bias the resolution to the deployment's country with `GEOCODER_COUNTRY_CODES` (e.g. `es` for the Madrid pack); `scripts/geocode_cache_clear.py` is the city-update path — clear the cache when the map data changes and the next lookup re-resolves.

## Coupons (promotions)

Per-tenant discount codes, the mirror of the pricing engine (#159): the same code can exist in two tenants with different values, and cross-tenant use looks identical to an unknown code.

| Method | Path | Description |
|---|---|---|
| POST | `/coupons` | Create a code for the caller's tenant (writer). `discount_kind` `percent` (≤100) or `amount`; optional `expires_at` and `max_redemptions` |
| GET | `/coupons` | List the caller's tenant's codes (open GET, tenant-scoped) |
| DELETE | `/coupons/{id}` | Deactivate (soft — the code stays for auditing) (writer) |

Pass `coupon_code` on `POST /pricing/quote` and an accepted coupon becomes an auditable line: `total` stays the fee before the discount, `discount` is the applied amount (capped at the fee — never a payout), `final_total` is what the order would pay. Every rejection — unknown, inactive, expired, budget spent — is the same 422, so codes cannot be probed for existence. A quote is a preview: pass `coupon_code` on `POST /orders` instead and the code REDEEMS — the fee is discounted, the `created` event carries the audit line (`list_fee`, `coupon_code`, `coupon_discount`), and `times_used` ticks.

## Payments

The checkout seam (#159 item 2): the pricing engine produces the amount, a payment provider settles it. The configured provider is the **sandbox** (`PAYMENT_PROVIDER=sandbox` — instant, deterministic, every receipt prefixed `sbx_`, no money moves); a real gateway is a config flip plus one class implementing the same `charge(amount, reference)` seam.

| Method | Path | Description |
|---|---|---|
| POST | `/orders/{id}/pay` | Settle the order's delivery fee (writer). Only while `pending` — pay at checkout, then dispatch; a second charge 409s with the existing receipt reference |
| GET | `/payments` | The caller's tenant's receipts (open GET, tenant-scoped — the 8th RLS table, migration 007) |
| GET | `/payments/{id}` | One receipt (404 outside the caller's tenant) |

A settlement appends a `paid` event to the order's event log (`amount`, `provider`, `provider_ref`) — the same feed the warehouse consumes, so revenue analytics never need the ops tables. The storefront's "Pay now (sandbox)" button drives the whole seam live.

## Menu (restaurant portal)

The kitchen's CRUD surface (#159 item 4): menu items per restaurant, tenant-scoped (9th RLS table, migration 008). The portal (`docs/restaurant-portal.html`) drives it; the storefront browses it read-only. V1 honesty: an order does not carry line items yet — the menu prices what the restaurant sells, the delivery fee prices the ride; a cart with per-order items is the documented next step.

| Method | Path | Description |
|---|---|---|
| POST | `/restaurants/{id}/menu-items` | Add an item — `name`, `price` (>0), optional `description`, `is_available` (writer) |
| GET | `/restaurants/{id}/menu-items` | The menu (open GET, tenant-scoped) |
| PATCH | `/menu-items/{id}` | Rename / reprice / flip availability ("sold out") (writer) |
| DELETE | `/menu-items/{id}` | Remove the item (writer; availability flips cover sold-out) |

Cross-tenant reads and writes are the same uniform 404 as an unknown id — no probing.

## Knowledge (per-tenant RAG)

The customer version of the knowledge bridge (#154): a tenant uploads markdown documents that become THEIR copilot's private knowledge — chunked, embedded, stored with `tenant_id` and scoped twice (explicit filter + RLS, migration 009 — the tenth tenant policy). The deployment-shared corpus (runbook, decision records, `tenant_id` NULL) reaches every tenant.

| Method | Path | Description |
|---|---|---|
| POST | `/knowledge/documents` | Upload a markdown doc as the caller tenant's private knowledge (writer); re-uploading a title replaces its chunks |
| GET | `/knowledge/documents` | The caller's private documents + the shared corpus (open GET, tenant-scoped) |

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/routes/demo` | Return the saved Tunis demo route as a GeoJSON FeatureCollection |
| GET | `/orders/{id}/route` | The order's captured road geometry (GeoJSON LineString, cached per pickup→dropoff pair); 404 if OSRM was unavailable at creation |

## Bulk import (onboarding R1)

| Method | Path | Description |
|---|---|---|
| POST | `/import/{entity}` | Bulk-import rows for `restaurants`, `drivers`, `customers` or `menu-items` (writer). CSV (`text/csv`, columns = the row fields, quote fields containing commas) or JSON (a list of row objects) |

The client has ADDRESSES, not coordinates: geo rows (restaurants/drivers/customers) carry an `address` column that the geocoder resolves (Nominatim + the permanent cache — re-imports pay only for new addresses; live calls are paced to the provider's ~1 req/s fair-use policy). Menu rows reference a `restaurant_id` of the caller's tenant.

**All-or-nothing per file with a per-row report.** Every row is validated and geocoded first; if ANY row fails, NOTHING is imported and the response is `422 import_rejected` with `error.details.report` naming each failed row and why (bad field → the validation message; unresolvable address → `address not found: …`). A happy file returns `201` with `{"entity", "imported", "report": [{"row", "status", "resolved_address", "latitude", "longitude", "cached"}, …]}`. An unreachable geocoder is `503 geocoder_unavailable` (retry — it is not the client's data).

## Analytics

| Method | Path | Description |
|---|---|---|
| GET | `/analytics/summary` | KPIs: status mix, revenue, avg fee, avg delivery time, fleet state |
| GET | `/analytics/metrics` | Prometheus text exposition |

## Copilot

`POST /copilot/chat` — the AI agent over the governed API (Phase 5). JWT required
(any authenticated role); every tool answer is scoped to the caller's tenant by RLS.

```json
// request
{ "question": "How many orders were delivered this week?" }
// response
{ "answer": "...", "tool_calls_used": 2, "provider": "openai:gpt-4o-mini",
  "degraded": false, "degraded_reason": null, "prompt_version": "v1", "usage_total": 1820 }
```

Guardrails reject before any model call (422): instruction-injection patterns,
empty questions, >2,000 characters. On total provider failure the endpoint still
answers 200 with `degraded: true` and `degraded_reason` naming each failure.
Full design: [ai.md](ai.md).

## Error envelope

Domain errors return a consistent body:

```json
{ "error": { "code": "no_driver_available", "message": "no eligible driver is currently available" } }
```

| Code | HTTP | Meaning |
|---|---|---|
| `not_found` | 404 | Entity does not exist |
| `invalid_transition` | 409 | Illegal lifecycle transition |
| `no_driver_available` | 409 | No eligible driver at dispatch time |
| `domain_error` | 400 | Other business-rule violation |
