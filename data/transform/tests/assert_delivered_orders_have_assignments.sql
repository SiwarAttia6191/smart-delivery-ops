-- Business-rule referential test across the event stream: our state machine
-- forbids reaching 'delivered' without a prior 'assigned' event (pending
-- orders can only be cancelled). A delivered order with no assignment means
-- the lifecycle or the pipeline dropped an event.

with delivered as (

    select distinct order_id
    from {{ ref('stg_order_events') }}
    where event_type = 'status'
      and status_to = 'delivered'

),

assigned as (

    select distinct order_id
    from {{ ref('stg_order_events') }}
    where event_type = 'assigned'

)

select d.order_id
from delivered d
left join assigned a on a.order_id = d.order_id
where a.order_id is null
