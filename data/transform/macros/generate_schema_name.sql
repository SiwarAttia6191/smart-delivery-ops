{# Schema resolution (#54): the model-level +schema names in
   dbt_project.yml (staging / analytics) stay verbatim for PRODUCTION
   builds (target schema `analytics`, set by the Makefile's main-branch
   mapping and the daily DAG). For every other target (`analytics_draft` -
   the safe profiles default) the custom name gains a `_draft` suffix, so
   a feature-branch build resolves ALL its relations into
   staging_draft / analytics_draft and can never touch the production
   schemas. The trade-off documented here before #54 ("a second
   environment must differ by database") is resolved: two schemas, one
   database, same raw sources. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set base = custom_schema_name | trim if custom_schema_name else target.schema -%}
    {%- if target.schema == 'analytics' -%}
        {{ base }}
    {%- else -%}
        {{ base }}_draft
    {%- endif -%}
{%- endmacro -%}
