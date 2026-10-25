{# dbt's default would prefix the target schema (analytics_staging); use the
   schema names in dbt_project.yml verbatim so the warehouse reads
   staging.* / analytics.* exactly as documented.
   Trade-off: no per-target schema isolation - a second environment (staging
   vs prod) must differ by database, not schema. Revisit if that day comes. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
