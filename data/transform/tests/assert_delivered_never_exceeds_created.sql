-- Funnel invariant: on any given day you can never deliver more orders than
-- were created (cumulative). Guards against double-counting delivered events
-- or dropping created events from the funnel mart.

with funnel as (

    select order_date, orders_created, orders_delivered
    from {{ ref('mart_daily_order_summary') }}

)

select *
from funnel
where orders_delivered > orders_created
