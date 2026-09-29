{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

with inputs as (
 select distinct wikidata_qid as qid, {{ mdp_cycle_week() }} as sitelinks_week
 from {{ ref('int_artist_identity') }} where wikidata_qid is not null
)
select *, cast('["mb_spine"]' as text) as _source_keys,
 {{ mdp_input_identity(['qid'], ['qid', 'sitelinks_week']) }} from inputs
