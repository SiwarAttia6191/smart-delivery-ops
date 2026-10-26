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
staging (typed, JSON flattened once) → four `analytics.*` marts. The
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
provider. **Source:** the [Geofabrik](https://download.geofabrik.de/) Tunisia
extract of OpenStreetMap (`tunisia-latest.osm.pbf`, ~84 MB, ODbL license) —
chosen because the seed data's coordinates are in Tunis.

| file | what it is |
|---|---|
| `tunisia-latest.osm.pbf` | raw OSM extract downloaded from Geofabrik |
| `tunisia-latest.osrm.*` | the prepared routing graph, built ONCE by the MLD pipeline (`osrm-extract` → `osrm-partition` → `osrm-customize`, car profile) |

**What it is NOT: live traffic.** OSRM speeds come from static OSM tags
(maxspeed, highway class) baked in at prepare time - free-flow estimates.
There is no live-traffic feed in self-hosted OSRM; the documented upgrade
path is `osrm-customize --segment-speed-file <csv>` refreshed periodically
(minutes, not real-time) - and our own realized delivery durations are a
legitimate source for that CSV. Consequence for evaluation: OSRM **distance**
is traffic-independent and solid ground truth; OSRM **durations** are not
valid ETA ground truth (see the ETA model's evaluation design, issue #26).

Why gitignored: ~826 MB of binary, regenerable in two commands:

```bash
curl -L -o data/osrm/tunisia-latest.osm.pbf \
  https://download.geofabrik.de/africa/tunisia-latest.osm.pbf
docker compose run --rm osrm-prepare     # builds the routing graph
docker compose up -d osrm                # serves /route on localhost:5000
```

The `osrm` container is what `scripts/benchmark_geo.py` uses as ground
truth and what the shadow distance provider queries.
