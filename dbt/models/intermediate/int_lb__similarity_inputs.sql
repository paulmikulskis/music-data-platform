{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

with inputs as (
 select distinct i.mb_artist_gid, g.algorithm, {{ mdp_cycle_week() }} as similarity_week
 from {{ ref('int_artist_identity') }} i
 cross join {{ ref('lb_similarity_algorithms') }} g
 where i.mb_artist_gid is not null and g.active
)
select *, cast('["mb_spine"]' as text) as _source_keys,
 {{ mdp_input_identity(['mb_artist_gid', 'algorithm'], ['mb_artist_gid', 'algorithm', 'similarity_week']) }} from inputs
