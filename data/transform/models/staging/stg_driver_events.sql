-- Driver availability transitions are the supply signal for dispatch and
-- forecasting. GPS pings are intentionally not collected by this MVP.
with source as (

    select * from {{ source('raw', 'driver_events') }}

)

select
    event_id,
    driver_id,
    event_type,
    schema_version,
    source_created_at,
    source_created_at AT TIME ZONE 'Africa/Tunis' as source_created_at_local,
    ingested_at,
    kafka_partition,
    kafka_offset,
    (payload->>'from_available')::boolean as from_available,
    (payload->>'to_available')::boolean as to_available,
    (payload->>'latitude')::numeric as latitude,
    (payload->>'longitude')::numeric as longitude,
    payload->>'vehicle' as vehicle,
    payload->>'reason' as reason,
    payload->>'order_id' as order_id
from source
