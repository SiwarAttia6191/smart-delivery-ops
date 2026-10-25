# Operations Dashboard (Phase 4)

Streamlit app rendering the KPI views: order trends, demand vs supply, driver
utilization, and forecasting. Consumes `/analytics/*` from the API.

Planned structure:

```
apps/dashboard/
├── app.py            # Streamlit entrypoint (make dashboard)
├── pages/            # overview / orders / drivers / forecasting
└── utils/            # charts.py, api_client.py
```

See docs/roadmap.md — Phase 4.
