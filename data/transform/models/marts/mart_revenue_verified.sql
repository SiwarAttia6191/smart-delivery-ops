-- Payment-verified revenue (#164 checkout seam): the money the payment
-- provider ACTUALLY settled, straight from the `paid` events — not the
-- quoted fees. This mart is the reconciliation layer: every row here has
-- a provider receipt (provider_ref), so "revenue" means settled money.
-- Coupon discounts are already inside the paid amount (the fee was
-- discounted before the charge), so this matches the storefront's
-- final_total to the cent.

with paid as (

    select
        order_id,
        source_created_at,
        source_created_at_local,
        paid_amount,
        payment_provider,
        payment_provider_ref
    from {{ ref('stg_order_events') }}
    where event_type = 'paid'

)

select
    source_created_at_local::date                       as paid_date,
    payment_provider,
    count(*)                                            as payments_settled,
    count(distinct payment_provider_ref)                as distinct_receipts,
    round(sum(paid_amount), 2)                          as verified_revenue,
    round(avg(paid_amount), 2)                          as avg_paid_amount,
    min(source_created_at)                              as first_payment_at,
    max(source_created_at)                              as last_payment_at
from paid
group by 1, 2
order by 1, 2
