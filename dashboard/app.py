"""Smart Delivery Ops — operational dashboard (Phase 4, #27).

One Streamlit page over the dbt marts: volume, fulfillment, revenue,
demand-vs-supply by zone, driver utilization, forecast-vs-actual, and the
per-tenant breakdown the isolation layer (#36) feeds via mart_tenant_daily.
Read-only by construction: the dashboard holds no write path to anything.

Run (compose builds this image): http://localhost:8501
"""

import os

import pandas as pd
import queries
import streamlit as st

st.set_page_config(page_title="Smart Delivery Ops", page_icon="🛵", layout="wide")

BUSINESS_TZ = os.environ.get("BUSINESS_TIMEZONE", "Africa/Tunis")


@st.cache_data(ttl=60, show_spinner="reading marts…")
def _daily() -> pd.DataFrame:
    return queries.daily_summary(queries.warehouse_engine())


@st.cache_data(ttl=60)
def _tenants() -> pd.DataFrame:
    return queries.tenant_daily(queries.warehouse_engine())


@st.cache_data(ttl=60)
def _tenant_names() -> dict[int, str]:
    return queries.tenant_names(queries.ops_engine())


@st.cache_data(ttl=60)
def _zones() -> pd.DataFrame:
    return queries.demand_supply_by_zone(queries.warehouse_engine())


@st.cache_data(ttl=60)
def _utilization() -> pd.DataFrame:
    return queries.driver_utilization(queries.warehouse_engine())


@st.cache_data(ttl=60)
def _forecast() -> pd.DataFrame:
    return queries.forecast_vs_actual(queries.ops_engine(), queries.warehouse_engine(), BUSINESS_TZ)


st.title("🛵 Smart Delivery Ops")
st.caption(
    f"Operational KPIs over the dbt marts · business timezone {BUSINESS_TZ} · "
    "read-only over the warehouse"
)

daily = _daily()
if daily.empty:
    st.warning("No history in the marts yet — run the seed + backfill first.")
    st.stop()

# ── Sidebar filters ──────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    dates = pd.to_datetime(daily["order_date"])
    span = st.slider(
        "Window (days back)",
        min_value=7,
        max_value=int((dates.max() - dates.min()).days) or 7,
        value=min(30, int((dates.max() - dates.min()).days) or 7),
        step=1,
    )
    cutoff = dates.max() - pd.Timedelta(days=span)
    scoped = daily[dates >= cutoff]

    tenants = _tenants()
    names = _tenant_names()
    tenant_options = {
        "All tenants": None,
        **{names.get(int(t), f"tenant {t}"): int(t) for t in sorted(tenants["tenant_id"].unique())},
    }
    tenant_choice = st.selectbox("Tenant", list(tenant_options))
    tenant_id = tenant_options[tenant_choice]

# ── KPI cards (with previous-window deltas) ──────────────────────────────
window = scoped
prev_start = cutoff - pd.Timedelta(days=span)
previous = daily[(dates >= prev_start) & (dates < cutoff)]


def _kpi(label, current: float, prev: float, fmt="{:,.0f}", lower_is_better=False):
    delta = None
    if prev:
        pct = (current - prev) / prev * 100
        delta = f"{pct:+.1f}% vs prev {span}d"
    st.metric(label, fmt.format(current), delta=delta)


c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    _kpi("Orders (created)", window["orders_created"].sum(), previous["orders_created"].sum())
with c2:
    fulfillment = 100.0 * window["orders_delivered"].sum() / max(window["orders_created"].sum(), 1)
    prev_f = 100.0 * previous["orders_delivered"].sum() / max(previous["orders_created"].sum(), 1)
    _kpi("Fulfillment rate", fulfillment, prev_f, "{:.1f}%")
with c3:
    _kpi("Revenue", window["revenue"].sum(), previous["revenue"].sum(), "{:,.0f}")
with c4:
    avg_min = window["avg_delivery_minutes"].mean()
    prev_min = previous["avg_delivery_minutes"].mean() or 0
    _kpi("Avg delivery", avg_min or 0, prev_min, "{:.1f} min")
with c5:
    _kpi(
        "Avg surge",
        window["avg_surge_multiplier"].mean() or 0,
        previous["avg_surge_multiplier"].mean() or 0,
        "{:.2f}×",
    )

st.divider()

# ── Volume + revenue ─────────────────────────────────────────────────────
left, right = st.columns([3, 2])
with left:
    st.subheader("Daily volume")
    vol = window.set_index(pd.to_datetime(window["order_date"]))[
        ["orders_created", "orders_delivered"]
    ]
    st.area_chart(vol)
with right:
    st.subheader("Daily revenue")
    rev = window.set_index(pd.to_datetime(window["order_date"]))[["revenue"]]
    st.bar_chart(rev)

# ── Demand vs supply by zone + utilization ───────────────────────────────
zcol, ucol = st.columns(2)
with zcol:
    st.subheader("Demand vs supply by zone (top 12)")
    zones = _zones()
    if tenant_id is not None:
        st.caption("zone view is deployment-wide (marts grain on zones)")
    if not zones.empty:
        top = zones.head(12)
        top["zone"] = top["zone_lat"].astype(str) + ", " + top["zone_lon"].astype(str)
        st.bar_chart(top.set_index("zone")[["orders", "avg_available_drivers"]])
    else:
        st.info("No zone data yet.")
with ucol:
    st.subheader("Driver utilization (top 10)")
    util = _utilization().head(10)
    if not util.empty:
        util = util.set_index(util["driver_id"].astype(str))[
            ["orders_assigned", "orders_delivered"]
        ]
        st.bar_chart(util)
    else:
        st.info("No assignment history yet.")

# ── Forecast vs actual ───────────────────────────────────────────────────
st.subheader("Forecast vs actual (city-wide, last 48h)")
fc = _forecast()
if not fc.empty:
    st.line_chart(fc.set_index("hour")[["predicted", "actual"]])
else:
    st.info("No forecasts in the serving window yet — the hourly DAG fills this.")

# ── Tenants ──────────────────────────────────────────────────────────────
st.subheader("Tenants")
if tenant_id is not None:
    t_scope = tenants[tenants["tenant_id"] == tenant_id]
    t_scope = t_scope[pd.to_datetime(tenants["business_date"]) >= cutoff]
    st.caption(f"scoped to {tenant_choice}")
else:
    t_scope = tenants[pd.to_datetime(tenants["business_date"]) >= cutoff]
if not t_scope.empty:
    tcol1, tcol2 = st.columns([2, 3])
    with tcol1:
        per_tenant = t_scope.groupby(
            t_scope["tenant_id"].map(lambda t: names.get(int(t), f"tenant {t}")), as_index=False
        )["orders"].sum()
        st.bar_chart(per_tenant.set_index(per_tenant.columns[0])["orders"])
    with tcol2:
        st.dataframe(
            t_scope.rename(columns={"tenant_id": "tenant"})[
                ["tenant", "business_date", "orders", "delivered", "revenue", "fulfillment_pct"]
            ],
            use_container_width=True,
            hide_index=True,
        )
else:
    st.info("No tenant-scoped history in the window.")

st.caption(
    "Sources: mart_daily_order_summary · mart_demand_supply_by_zone_hour · "
    "mart_driver_utilization · mart_demand_timeseries · mart_tenant_daily · "
    "ops.demand_forecasts. Everything except the last two is dbt output."
)
