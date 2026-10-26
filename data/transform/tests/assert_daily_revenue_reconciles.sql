-- Cross-mart reconciliation: the revenue in the daily funnel mart must equal
-- the sum of quoted fees in the pricing-history mart for every day. Both are
-- built from created events, so any drift between them (a new filter added to
-- one mart and forgotten in the other, a changed definition of "fee") fails
-- here instead of in a stakeholder's dashboard.

with from_funnel as (

    select order_date, revenue
    from {{ ref('mart_daily_order_summary') }}

),

from_pricing as (

    select
        quoted_at_local::date as order_date,
        sum(delivery_fee) as revenue
    from {{ ref('mart_pricing_history') }}
    group by 1

)

select
    coalesce(f.order_date, p.order_date) as order_date,
    f.revenue                            as funnel_revenue,
    p.revenue                            as pricing_revenue
from from_funnel f
full outer join from_pricing p on p.order_date = f.order_date
where f.revenue is distinct from p.revenue
