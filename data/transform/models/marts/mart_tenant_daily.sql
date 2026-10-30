-- Per-tenant daily funnel: the SaaS dimension the dashboard's tenant filter
-- reads (#36 follow-through, #27). Additive to the existing marts: the
-- deployment-wide grains (and the ML feature feed) are deliberately NOT
-- re-grained per tenant.
with created as (

    select
        tenant_id,
        order_id,
        source_created_at_local,
        delivery_fee
    from {{ ref('stg_order_events') }}
    where event_type = 'created'

),

delivered as (

    select order_id
    from {{ ref('stg_order_events') }}
    where event_type = 'status'
      and status_to = 'delivered'

)

select
    created.tenant_id,
    created.source_created_at_local::date as business_date,
    count(distinct created.order_id)      as orders,
    count(distinct delivered.order_id)    as delivered,
    round(sum(created.delivery_fee), 2)   as revenue,
    round(
        100.0 * count(distinct delivered.order_id) / nullif(count(distinct created.order_id), 0),
        1
    )                                     as fulfillment_pct
from created
left join delivered using (order_id)
group by 1, 2
