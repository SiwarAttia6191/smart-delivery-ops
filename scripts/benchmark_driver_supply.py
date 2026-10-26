"""Summarize demand/supply feature coverage after the dbt build.

Run after `docker compose run --rm dbt build`:
    python -m scripts.benchmark_driver_supply
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from core.config import get_settings

SUMMARY_SQL = text(
    """
    select
        count(*) as feature_cells,
        count(*) filter (where orders > 0) as demand_cells,
        count(*) filter (where orders > 0 and available_drivers > 0) as covered_demand_cells,
        count(*) filter (where orders > 0 and available_drivers = 0) as demand_without_supply_cells,
        count(*) filter (where orders = 0 and available_drivers > 0) as supply_only_cells,
        coalesce(sum(orders), 0) as orders,
        coalesce(sum(available_drivers), 0) as available_driver_hours,
        avg(drivers_per_order) filter (where orders > 0) as avg_drivers_per_order
    from analytics.mart_demand_supply_by_zone_hour
    """
)
DATE_RANGE_SQL = text(
    """
    select min(business_date), max(business_date)
    from analytics.mart_demand_supply_by_zone_hour
    """
)


def render_report(summary: dict, date_range: tuple[object, object]) -> str:
    demand_cells = summary["demand_cells"]
    covered_cells = summary["covered_demand_cells"]
    coverage = round(covered_cells / demand_cells * 100, 1) if demand_cells else None
    coverage_text = f"{coverage}%" if coverage is not None else "n/a (no demand cells)"
    avg_ratio = summary["avg_drivers_per_order"]
    avg_ratio_text = f"{avg_ratio:.3f}" if avg_ratio is not None else "n/a"
    return "\n".join(
        [
            "# Driver supply feature sanity check",
            "",
            f"* Business-date range: {date_range[0] or 'no data'} to {date_range[1] or 'no data'}",
            f"* Feature cells: {summary['feature_cells']}",
            f"* Demand cells with at least one available driver: {covered_cells}/{demand_cells} "
            f"({coverage_text})",
            f"* Demand cells without supply: {summary['demand_without_supply_cells']}",
            f"* Supply-only cells: {summary['supply_only_cells']}",
            f"* Orders represented: {summary['orders']}",
            f"* Available driver-hours represented (summed across zone/hour cells): "
            f"{summary['available_driver_hours']}",
            f"* Average drivers per order in demand cells: {avg_ratio_text}",
            "",
            "This is a coverage/sanity report, not a causal or forecasting benchmark. "
            "Demand uses order pickup zones; availability is integrated over the hour "
            "using the last evented driver location. GPS pings and live movement are "
            "out of scope.",
            "",
        ]
    )


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.warehouse_database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            summary = dict(connection.execute(SUMMARY_SQL).mappings().one())
            date_range = connection.execute(DATE_RANGE_SQL).one()
    finally:
        engine.dispose()

    report = render_report(summary, date_range)
    print(report)
    output_dir = Path("data/benchmarks")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"driver_supply_sanity_{datetime.now(UTC):%Y%m%d_%H%M%S}.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"report saved: {output_path}")


if __name__ == "__main__":
    main()
