-- One row per (tenant, day) - the dashboard's tenant filter would double-
-- count revenue if the grain ever fanned out.
select tenant_id, business_date, count(*) as dupes
from {{ ref('mart_tenant_daily') }}
group by 1, 2
having count(*) > 1
