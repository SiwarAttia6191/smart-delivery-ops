-- Per-tenant daily funnel: the SaaS dimension the dashboard's tenant filter
-- reads (#36 follow-through, #27). Additive to the existing marts: the
-- deployment-wide grains (and the ML feature feed) are deliberately NOT
-- re-grained per tenant.
with created as (

    select
        tenant_id,
        order_id,
        source_created_at,
        source_created_at_local,
        delivery_fee,
        surge_multiplier
    from {{ ref('stg_order_events') }}
    where event_type = 'created'

),

delivered as (

    select
        order_id,
        -- UTC instant, to match the created side in the duration math
        source_created_at as delivered_at
    from {{ ref('stg_order_events') }}
    where event_type = 'status'
      and status_to = 'delivered'

),

per_order as (

    select
        c.tenant_id,
        c.order_id,
        c.source_created_at_local,
        c.delivery_fee,
        c.surge_multiplier,
        d.delivered_at,
        -- duration math on UTC instants; the date bucket below is local
        round(extract(epoch from (d.delivered_at - c.source_created_at)) / 60.0, 1)
            as actual_delivery_minutes
    from created c
    left join delivered d on d.order_id = c.order_id

)

select
    tenant_id,
    source_created_at_local::date          as business_date,
    count(*)                               as orders,
    count(delivered_at)                    as delivered,
    round(sum(delivery_fee), 2)            as revenue,
    round(
        100.0 * count(delivered_at) / nullif(count(*), 0),
        1
    )                                      as fulfillment_pct,
    round(avg(actual_delivery_minutes), 1) as avg_delivery_minutes,
    round(avg(surge_multiplier), 2)        as avg_surge_multiplier
from per_order
group by 1, 2
