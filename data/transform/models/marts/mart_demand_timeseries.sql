-- Time series of demand: one row per business-date × local-hour × zone.
-- Unlike mart_demand_by_zone_hour (a profile aggregated across days), THIS is
-- the training input for demand forecasting (#25) — forecasting needs a
-- time-ordered series, not an average shape.

with created as (

    select
        source_created_at_local,
        round(dropoff_latitude, 2)  as zone_lat,
        round(dropoff_longitude, 2) as zone_lon
    from {{ ref('stg_order_events') }}
    where event_type = 'created'
      and dropoff_latitude is not null
      and dropoff_longitude is not null

)

select
    source_created_at_local::date                as business_date,
    extract(hour from source_created_at_local)::int as business_hour,
    zone_lat,
    zone_lon,
    count(*)                                     as orders
from created
group by 1, 2, 3, 4
