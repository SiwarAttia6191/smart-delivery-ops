-- Per-courier workload: assignments, completions, and match efficiency.
-- Driver attribution comes from each order's assigned event; completions from
-- the delivered status event of orders that driver was assigned to.

with assignments as (

    -- Latest assignment per order: the state machine currently forbids
    -- reassignment, but if Phase 3 allocation ever introduces it, a naive
    -- join would fan out and double-count. Attribute to the LAST courier.
    select
        order_id,
        driver_id,
        match_distance_km,
        vehicle,
        source_created_at as assigned_at
    from (
        select
            *,
            row_number() over (partition by order_id order by event_id desc) as rn
        from {{ ref('stg_order_events') }}
        where event_type = 'assigned'
    ) ranked
    where rn = 1

),

delivered as (

    select order_id, source_created_at as delivered_at
    from {{ ref('stg_order_events') }}
    where event_type = 'status'
      and status_to = 'delivered'

),

per_driver_order as (

    select
        a.driver_id,
        a.vehicle,
        a.match_distance_km,
        d.delivered_at,
        round(extract(epoch from (d.delivered_at - a.assigned_at)) / 60.0, 1)
            as assigned_to_delivered_minutes
    from assignments a
    left join delivered d on d.order_id = a.order_id

)

select
    driver_id,
    count(*)                                      as orders_assigned,
    count(delivered_at)                           as orders_delivered,
    round(avg(match_distance_km), 2)              as avg_match_distance_km,
    round(avg(assigned_to_delivered_minutes), 1)  as avg_assignment_to_delivery_minutes,
    string_agg(distinct vehicle, ', ')            as vehicles_used
from per_driver_order
group by driver_id
