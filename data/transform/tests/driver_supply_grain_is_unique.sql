select
    driver_zone_lat,
    driver_zone_lon,
    supply_date,
    supply_hour
from {{ ref('mart_driver_supply_by_zone_hour') }}
group by 1, 2, 3, 4
having count(*) > 1
