{# dbt's default would prefix the target schema (analytics_staging); use the
   schema names in dbt_project.yml verbatim so the warehouse reads
   staging.* / analytics.* exactly as documented. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
