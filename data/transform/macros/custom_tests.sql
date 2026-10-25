{# Non-negative numeric guard: fails on any row below zero (fees, revenue,
   counts). Reusable across marts. #}
{% test non_negative(model, column_name) %}

select *
from {{ model }}
where {{ column_name }} < 0

{% endtest %}


{# Bounds guard for ratios and multipliers that have a known legal range —
   e.g. our surge cap is 2.0 by design (tier 3), so anything outside
   [min, max] means an upstream rule or data problem. #}
{% test between(model, column_name, min, max) %}

select *
from {{ model }}
where {{ column_name }} < {{ min }}
   or {{ column_name }} > {{ max }}

{% endtest %}
