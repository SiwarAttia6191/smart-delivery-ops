# API Reference

Interactive versions: **/docs** (Swagger UI) and **/redoc** when the API is running.

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

## Routes

| Method | Path | Description |
|---|---|---|
| GET | `/routes/demo` | Return the saved Tunis demo route as a GeoJSON FeatureCollection |

## Analytics

| Method | Path | Description |
|---|---|---|
| GET | `/analytics/summary` | KPIs: status mix, revenue, avg fee, avg delivery time, fleet state |
| GET | `/analytics/metrics` | Prometheus text exposition |

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
