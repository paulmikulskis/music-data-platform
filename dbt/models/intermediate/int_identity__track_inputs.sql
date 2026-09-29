-- depends_on: {{ ref('bronze_close__hourly') }}
{#- design: every track item in the hourly manifest with its surface enrichment, incremental by
    track key. A build recomputes every (platform, platform_track_id) touched by a dump stamped above
    the relation's high-water mark in any joined input (items, snapshots and page enrichment,
    hydration), from all manifest-visible rows through the bound close_no, so a key's inputs depend on
    the manifest alone and a snapshot stamped one close after its items still meets them. It carries no
    priority flag. A replay (cycle_id set), and a list-mode cycle, rebuild it in full. -#}
{%- set ctx = mdp_context() -%}
{%- set stamped = is_incremental() and ctx.manifest_mode == 'stamp' and ctx.close_no is not none -%}
{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['platform', 'platform_track_id'],
          tags=['cadence:hourly', 'scope:global'], full_refresh=(var('cycle_id', none) is not none),
          pre_hook="{{ mdp_hash_join_plan() }}", post_hook=["{{ mdp_indexes([['key', 'platform, platform_track_id', ''], ['hwm', '_hwm_close_no', '']]) }}",
                     "{{ mdp_analyze() }}"]) }}
{%- if stamped %}
with touched as ({{ mdp_track_inputs_touched(this, ctx) }})
select i.*, cast({{ ctx.close_no }} as bigint) as _hwm_close_no from ({{ mdp_track_inputs('touched') }}) i
{%- else %}
select i.*, cast({{ ctx.close_no if ctx.close_no is not none else -1 }} as bigint) as _hwm_close_no
from ({{ mdp_track_inputs() }}) i
{%- endif %}
