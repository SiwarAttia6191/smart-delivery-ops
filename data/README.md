# Data Engineering Layer (Phase 2)

```
data/
├── ingestion/    # stream + batch ingestion from order/driver/pricing events
├── pipeline/     # ETL: etl.py, transforms.py, validators.py
├── warehouse/    # schema/ (DDL) + queries/ (curated analytics SQL)
└── raw/          # immutable raw event landings (gitignored)
```

Warehouse tables planned: `orders`, `drivers`, `customers`, `restaurants`,
`order_events`, `demand_forecast`, `pricing_history`, `assignment_history`.
