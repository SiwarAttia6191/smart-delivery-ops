-- The pricing engine's decision log, straight from the event stream: every
-- quote's distance, fee, and surge. This is the training/evaluation feed for
-- the Phase 3 pricing model.

select
    order_id,
    source_created_at            as quoted_at,
    distance_km,
    delivery_fee,
    surge_multiplier,
    -- what the engine actually charged per kilometre, for skew analysis
    round(delivery_fee / nullif(distance_km, 0), 2) as fee_per_km
from {{ ref('stg_order_events') }}
where event_type = 'created'
