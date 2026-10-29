# Data layer

What actually lives here, where it comes from, and how to regenerate it.

```
data/
├── transform/    # dbt project: staging + warehouse marts (the medallion layer)
├── benchmarks/   # committed benchmark reports (geo distance today, ML later)
│   └── driver_supply_sanity_*.md  # demand-vs-available-supply feature coverage
├── osrm/         # OSRM map data for Tunisia — gitignored, ~826 MB (see below)
├── schema/       # (empty scaffolding, pre-Phase 2)
├── ingestion/    # (empty scaffolding — ingestion lives in apps/worker/ingest.py)
├── pipeline/     # (empty scaffolding — transformations live in data/transform/)
├── raw/          # (empty — the raw layer is the warehouse DB's raw schema)
└── warehouse/    # (empty scaffolding — the warehouse is Postgres, not files)
```

## `transform/` — the dbt project

Sources (`raw.order_events`, landed by the Kafka ingestion consumer) →
staging (typed, JSON flattened once, local grains in the configured `BUSINESS_TIMEZONE`) → seven `analytics.*` marts. The
medallion layering and every model are documented in the main README under
"Warehouse transformations". Run with `make dbt`.

## `benchmarks/` — committed report artifacts

Output of `scripts/benchmark_geo.py`: distance-provider accuracy against
OSRM road-distance ground truth, per distance bucket (MAE/RMSE/MAPE/bias).
Geo benchmark reports embed their dataset snapshot (`max(event_id)` + git SHA)
so a run is reproducible to the exact rows. These ARE committed — they are the
evidence behind the champion/challenger decisions.

After rebuilding dbt, run `python -m scripts.benchmark_driver_supply` to
write a demand/supply coverage report. It checks same-zone, local-date/hour
feature coverage; it is a sanity report, not a causal or forecasting benchmark.

## `osrm/` — OSRM map data (gitignored)

Road-network data for the distance benchmark and the OSRM distance
provider. **Source:** a [Geofabrik](https://download.geofabrik.de/) OSM
extract — every region on Earth has one; the region is configuration, not
code (#78 region pack). The default deployment uses the Tunisia extract
(`tunisia-latest.osm.pbf`, ~84 MB, ODbL license), matching the seed data's
Tunis coordinates; second regions live in their own subdirectory
(`ile-de-france/` for the verified Paris deployment, `./data/osrm/<extract_id>/`).

| file | what it is |
|---|---|
| `<extract_id>-latest.osm.pbf` | raw OSM extract downloaded from Geofabrik |
| `<extract_id>-latest.osrm.*` | the prepared routing graph, built ONCE by the MLD pipeline (`osrm-extract` → `osrm-partition` → `osrm-customize`, car profile) |

**What it is NOT: live traffic.** OSRM speeds come from static OSM tags
(maxspeed, highway class) baked in at prepare time - free-flow estimates.
There is no live-traffic feed in self-hosted OSRM; the documented upgrade
path is `osrm-customize --segment-speed-file <csv>` refreshed periodically
(minutes, not real-time) - and our own realized delivery durations are a
legitimate source for that CSV. Consequence for evaluation: OSRM **distance**
is traffic-independent and solid ground truth; OSRM **durations** are not
valid ETA ground truth (see the ETA model's evaluation design, issue #26).

Why gitignored: regenerable binaries, driven by the city manifest:

```bash
# any region: manifest names the extract + download URL
python scripts/fetch_osrm_extract.py --region <city>     # host download
OSRM_EXTRACT_ID=<id> OSRM_DATA_DIR=data/osrm/<id> \
  docker compose run --rm osrm-prepare                   # builds the routing graph
OSRM_EXTRACT_ID=<id> OSRM_DATA_DIR=data/osrm/<id> \
  docker compose --profile tools up -d osrm              # serves /route
# defaults (OSRM_EXTRACT_ID=tunisia, OSRM_DATA_DIR=./data/osrm) = the Tunisia demo
```

The `osrm` container is what `scripts/benchmark_geo.py` uses as ground
truth and what the shadow distance provider queries.

## Demo route

The saved Tunis OSRM route is checked in beside the project demo page as
[`../docs/route.geojson`](../docs/route.geojson).
The API serves it at `GET /routes/demo`, and the project demo page renders it
on an OpenStreetMap-backed Leaflet map. The GeoJSON is a static route snapshot;
the OSRM service is only needed to generate a new route, not to display this one.
