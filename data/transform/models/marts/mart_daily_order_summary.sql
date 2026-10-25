-- One row per calendar day: funnel volumes, revenue, and delivery speed.
-- Delivery minutes join each order's created and delivered events (the event
-- log is the only source of truth — the ops orders table is never read).

with created as (

    select
        order_id,
        source_created_at_local,
        distance_km,
        delivery_fee,
        surge_multiplier
    from {{ ref('stg_order_events') }}
    where event_type = 'created'

),

delivered as (

    select
        order_id,
        source_created_at as delivered_at
    from {{ ref('stg_order_events') }}
    where event_type = 'status'
      and status_to = 'delivered'

),

per_order as (

    select
        c.order_id,
        c.source_created_at,
        c.distance_km,
        c.delivery_fee,
        c.surge_multiplier,
        d.delivered_at,
        round(extract(epoch from (d.delivered_at - c.source_created_at)) / 60.0, 1)
            as actual_delivery_minutes
    from created c
    left join delivered d on d.order_id = c.order_id

)

select
    source_created_at_local::date                 as order_date,
    count(*)                                      as orders_created,
    count(delivered_at)                           as orders_delivered,
    round(avg(actual_delivery_minutes), 1)        as avg_delivery_minutes,
    sum(delivery_fee)                             as revenue,
    round(avg(distance_km), 2)                    as avg_distance_km,
    round(avg(surge_multiplier), 2)               as avg_surge_multiplier
from per_order
group by 1
