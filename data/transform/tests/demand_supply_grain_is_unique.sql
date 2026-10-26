select
    zone_lat,
    zone_lon,
    business_date,
    business_hour
from {{ ref('mart_demand_supply_by_zone_hour') }}
group by 1, 2, 3, 4
having count(*) > 1
