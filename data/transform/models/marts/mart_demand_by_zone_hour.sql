-- Where and when demand happens: dropoff zones (~1 km grid cells) × hour of
-- day. This is the table the Phase 3 demand forecasting model will train on.
-- Zones come from the coordinates riding on the created event — the ops
-- tables are never read.

with created as (

    select
        order_id,
        source_created_at_local,
        dropoff_latitude,
        dropoff_longitude,
        delivery_fee,
        surge_multiplier
    from {{ ref('stg_order_events') }}
    where event_type = 'created'

),

zoned as (

    select
        -- ~0.01° grid ≈ 1 km squares; good enough to see city hotspots
        round(dropoff_latitude, 2)   as dropoff_zone_lat,
        round(dropoff_longitude, 2)  as dropoff_zone_lon,
        extract(hour from source_created_at_local)::int as order_hour,
        delivery_fee,
        surge_multiplier
    from created
    where dropoff_latitude is not null
      and dropoff_longitude is not null

)

select
    dropoff_zone_lat,
    dropoff_zone_lon,
    order_hour,
    count(*)                     as orders,
    sum(delivery_fee)            as total_fees,
    round(avg(surge_multiplier), 2) as avg_surge_multiplier
from zoned
group by 1, 2, 3
