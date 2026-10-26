-- Forecasting training input: a time-ordered demand series per zone
-- (business-date × local-hour × ~1 km zone). Grain guard: exactly one row
-- per zone/date/hour — duplicates would silently bias any model trained
-- on this table.

select
    zone_lat,
    zone_lon,
    business_date,
    business_hour,
    orders
from {{ ref('mart_demand_timeseries') }}
where (zone_lat, zone_lon, business_date, business_hour) in (
    select zone_lat, zone_lon, business_date, business_hour
    from {{ ref('mart_demand_timeseries') }}
    group by 1, 2, 3, 4
    having count(*) > 1
)
