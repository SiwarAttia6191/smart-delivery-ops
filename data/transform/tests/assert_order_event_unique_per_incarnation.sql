-- (incarnation, event_id) is the raw layer's dedupe key (m4, #76):
-- two ops incarnations may legitimately reuse id ranges, but the PAIR
-- must never repeat.
select incarnation, event_id, count(*) as dupes
from raw.order_events
group by incarnation, event_id
having count(*) > 1
