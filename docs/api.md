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

## Coupons (promotions)

Per-tenant discount codes, the mirror of the pricing engine (#159): the same code can exist in two tenants with different values, and cross-tenant use looks identical to an unknown code.

| Method | Path | Description |
|---|---|---|
| POST | `/coupons` | Create a code for the caller's tenant (writer). `discount_kind` `percent` (≤100) or `amount`; optional `expires_at` and `max_redemptions` |
| GET | `/coupons` | List the caller's tenant's codes (open GET, tenant-scoped) |
| DELETE | `/coupons/{id}` | Deactivate (soft — the code stays for auditing) (writer) |

Pass `coupon_code` on `POST /pricing/quote` and an accepted coupon becomes an auditable line: `total` stays the fee before the discount, `discount` is the applied amount (capped at the fee — never a payout), `final_total` is what the order would pay. Every rejection — unknown, inactive, expired, budget spent — is the same 422, so codes cannot be probed for existence. A quote is a preview: `times_used` only ticks when an order is placed with the code (wired with the ordering surface, #159 item 1).

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/routes/demo` | Return the saved Tunis demo route as a GeoJSON FeatureCollection |
| GET | `/orders/{id}/route` | The order's captured road geometry (GeoJSON LineString, cached per pickup→dropoff pair); 404 if OSRM was unavailable at creation |

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
