"""The copilot's tool registry (#120 Phase A): typed schemas over the
platform's EXISTING read logic. Three properties are architectural:

1. READ-ONLY — no tool mutates anything; writes are a Phase B concern
   behind a human-approval gate.
2. GOVERNED — every tool executes with the REQUEST's session, so the RLS
   GUC pinned by the tenant middleware scopes each answer to the caller's
   tenant. The agent cannot see rows its user cannot see.
3. EXACT — tools return the same computed numbers the API endpoints
   serve; the LLM narrates results, it never invents them.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from apps.api.routes import analytics as analytics_routes


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema subset: {key: {"type": ...}}
    func: Callable[..., Any]

    def validate(self, args: dict[str, Any]) -> None:
        missing = [k for k in self.parameters if k not in args]
        if missing:
            raise ValueError(f"missing argument(s) for {self.name}: {missing}")


def _analytics_summary(db: Session) -> dict:
    # the exact computation the /analytics/summary endpoint serves
    return analytics_routes._summary(db).model_dump()


def _orders_recent(db: Session, limit: int = 5) -> list[dict]:
    from domain.services import order_service

    orders = order_service.list_orders(db, limit=max(1, min(int(limit), 20)))
    return [
        {
            "id": o.id,
            "status": o.status.value,
            "delivery_fee": o.delivery_fee,
            "distance_km": o.distance_km,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in orders
    ]


def _analytics_by_tenant(db: Session) -> list[Any]:  # pydantic slices; the executor serializes
    # the per-tenant slice the dashboard consumes; each slice is computed
    # in its own RLS scope (see the endpoint's docstring)
    return analytics_routes.by_tenant(db)


def _search_docs(db: Session, query: str) -> list[dict[str, Any]]:
    from apps.copilot.retriever import search_docs

    return search_docs(db, query)


def _forecast_next_24h(db: Session) -> list[dict]:
    from domain.models import DemandForecast

    rows = db.query(DemandForecast).order_by(DemandForecast.forecast_hour_utc).limit(48).all()
    return [
        {
            "zone": f"({r.zone_lat}, {r.zone_lon})",
            "forecast_hour_utc": r.forecast_hour_utc.isoformat() if r.forecast_hour_utc else None,
            "predicted_orders": r.predicted_orders,
        }
        for r in rows
    ]


def build_registry() -> dict[str, ToolSpec]:
    specs = [
        ToolSpec(
            name="analytics_summary",
            description="Operational KPIs: total orders, status mix, delivered revenue, "
            "average delivery minutes, driver availability.",
            parameters={},
            func=_analytics_summary,
        ),
        ToolSpec(
            name="orders_recent",
            description="The most recent orders with status, fee, and distance.",
            parameters={"limit": {"type": "int"}},
            func=_orders_recent,
        ),
        ToolSpec(
            name="analytics_by_tenant",
            description="The operational summary, one slice per tenant.",
            parameters={},
            func=_analytics_by_tenant,
        ),
        ToolSpec(
            name="forecast_next_24h",
            description="The ML demand forecasts for the next hours, per zone.",
            parameters={},
            func=_forecast_next_24h,
        ),
        ToolSpec(
            name="search_docs",
            description="Search the platform's operational knowledge (runbook, "
            "incident summaries, decisions) — use for HOW questions, not for numbers.",
            parameters={"query": {"type": "str"}},
            func=_search_docs,
        ),
    ]
    return {s.name: s for s in specs}


def tool_schemas(registry: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    """The provider-facing tool list (name, description, JSON schema)."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "parameters": {"type": "object", "properties": s.parameters},
        }
        for s in registry.values()
    ]


def execute_tool(
    registry: dict[str, ToolSpec], db: Session, name: str, args: dict[str, Any]
) -> str:
    """Run one tool call with the request's session; a malformed call is a
    caught error (the model gets the message, not a crash)."""
    spec = registry.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        spec.validate(args)
        result = spec.func(db, **args)
        return json.dumps({"result": result}, default=str)
    except Exception as exc:  # noqa: BLE001 - the model sees the error, the API never breaks
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
