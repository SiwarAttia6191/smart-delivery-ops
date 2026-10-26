-- Feature sanity view: compare customer demand against online driver supply
-- at the same ~1 km zone and Africa/Tunis local hour.
with demand as (

    select
        round(pickup_latitude, 2) as zone_lat,
        round(pickup_longitude, 2) as zone_lon,
        source_created_at_local::date as business_date,
        extract(hour from source_created_at_local)::int as business_hour,
        count(*) as orders
    from {{ ref('stg_order_events') }}
    where event_type = 'created'
      and pickup_latitude is not null
      and pickup_longitude is not null
    group by 1, 2, 3, 4

),

supply as (

    select
        driver_zone_lat as zone_lat,
        driver_zone_lon as zone_lon,
        supply_date as business_date,
        supply_hour as business_hour,
        available_drivers
    from {{ ref('mart_driver_supply_by_zone_hour') }}

)

select
    coalesce(d.zone_lat, s.zone_lat) as zone_lat,
    coalesce(d.zone_lon, s.zone_lon) as zone_lon,
    coalesce(d.business_date, s.business_date) as business_date,
    coalesce(d.business_hour, s.business_hour) as business_hour,
    coalesce(d.orders, 0) as orders,
    coalesce(s.available_drivers, 0) as available_drivers,
    case
        when coalesce(d.orders, 0) = 0 then null
        else round(coalesce(s.available_drivers, 0)::numeric / d.orders, 3)
    end as drivers_per_order
from demand d
full outer join supply s
    on d.zone_lat = s.zone_lat
   and d.zone_lon = s.zone_lon
   and d.business_date = s.business_date
   and d.business_hour = s.business_hour
