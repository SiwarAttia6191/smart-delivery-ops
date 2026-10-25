-- One clean, typed row per event. Payload flattening happens exactly once,
-- here — the marts never touch JSON. Events older than the coordinate
-- enrichment keep working: missing payload keys simply come through as NULL.
-- Parentheses matter: payload->>'key'::int would cast the *literal* to int.
with source as (

    select * from {{ source('raw', 'order_events') }}

)

select
    event_id,
    order_id,
    event_type,
    schema_version,
    source_created_at,
    ingested_at,
    -- created events
    (payload->>'customer_id')::int                     as customer_id,
    (payload->>'restaurant_id')::int                   as restaurant_id,
    (payload->>'distance_km')::numeric                 as distance_km,
    (payload->>'delivery_fee')::numeric                as delivery_fee,
    (payload->>'surge_multiplier')::numeric            as surge_multiplier,
    (payload->>'eta_minutes')::numeric                 as eta_minutes,
    (payload->>'pickup_latitude')::numeric             as pickup_latitude,
    (payload->>'pickup_longitude')::numeric            as pickup_longitude,
    (payload->>'dropoff_latitude')::numeric            as dropoff_latitude,
    (payload->>'dropoff_longitude')::numeric           as dropoff_longitude,
    -- assigned events
    (payload->>'driver_id')::int                       as driver_id,
    (payload->>'match_distance_km')::numeric           as match_distance_km,
    payload->>'vehicle'                                as vehicle,
    -- status events
    payload->>'from'                                   as status_from,
    payload->>'to'                                     as status_to

from source
