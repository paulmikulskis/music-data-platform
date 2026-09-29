{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

with inputs as (
 select distinct mb_artist_gid, {{ mdp_cycle_day() }} as popularity_day
 from {{ ref('int_artist_identity') }} where mb_artist_gid is not null
)
select *, cast('["mb_spine"]' as text) as _source_keys,
 {{ mdp_input_identity(['mb_artist_gid'], ['mb_artist_gid', 'popularity_day']) }} from inputs
