-- Channel attribution (#226): created orders and revenue split by the
-- order's declared source (storefront / marketplace / phone). The data
-- the restaurant needs to decide which orders to keep on-platform: the
-- marketplace takes 25-40% per order, the first-party channel costs the
-- payment processing. Every row comes from the same governed event log.

with created as (

    select
        order_id,
        source_created_at,
        source_created_at_local,
        delivery_fee,
        order_source
    from {{ ref('stg_order_events') }}
    where event_type = 'created'

)

select
    source_created_at_local::date                     as order_date,
    coalesce(order_source, 'unattributed')            as order_source,
    count(*)                                          as orders_created,
    round(sum(delivery_fee), 2)                       as revenue,
    round(avg(delivery_fee), 2)                       as avg_order_value
from created
group by 1, 2
order by 1, 2
