-- Reconstruct average available-driver supply during each local hour.
-- Availability intervals end at the next transition for that driver. The
-- last known available interval is carried through the current hour.
--
-- #60: location_update events (hysteresis-based, emitted only for real
-- movement) participate in the transition sequence, so an available
-- driver's interval SPLITS at each move and supply tracks their current
-- zone. The metric converges from last-evented-location toward
-- current-location as update frequency rises; a driver gone silent past
-- the stale window is swept offline by the worker (stale_sweep), closing
-- their interval instead of counting forever.
with transitions as (

    select
        driver_id,
        event_id,
        source_created_at_local as changed_at,
        lead(source_created_at_local) over (
            partition by driver_id order by source_created_at_local, event_id
        ) as next_change_at,
        coalesce(to_available, available) as to_available,
        latitude,
        longitude
    from {{ ref('stg_driver_events') }}
    where event_type = 'availability'
       or (event_type = 'location_update' and available)

),

available_intervals as (

    select
        driver_id,
        round(latitude, 2) as driver_zone_lat,
        round(longitude, 2) as driver_zone_lon,
        changed_at as available_from,
        coalesce(
            next_change_at,
            date_trunc('hour', current_timestamp AT TIME ZONE 'Africa/Tunis')
                + interval '1 hour'
        ) as available_until
    from transitions
    where to_available
      and latitude is not null
      and longitude is not null

),

driver_hours as (

    select
        i.driver_id,
        i.driver_zone_lat,
        i.driver_zone_lon,
        h.hour_start,
        extract(
            epoch from (
                least(i.available_until, h.hour_start + interval '1 hour')
                - greatest(i.available_from, h.hour_start)
            )
        ) / 3600.0 as available_fraction
    from available_intervals i
    cross join lateral generate_series(
        date_trunc('hour', i.available_from),
        date_trunc('hour', i.available_until),
        interval '1 hour'
    ) as h(hour_start)
    where h.hour_start < i.available_until
      and h.hour_start + interval '1 hour' > i.available_from

),

driver_zone_hours as (

    select
        driver_id,
        driver_zone_lat,
        driver_zone_lon,
        hour_start,
        least(sum(available_fraction), 1.0) as driver_fraction
    from driver_hours
    group by 1, 2, 3, 4

)

select
    driver_zone_lat,
    driver_zone_lon,
    hour_start::date as supply_date,
    extract(hour from hour_start)::int as supply_hour,
    round(sum(driver_fraction)::numeric, 3) as available_drivers
from driver_zone_hours
group by 1, 2, 3, 4
