-- depends_on: {{ ref('gold_invoke__track_isrc_crosswalk') }}
-- The rows visible in this cycle's manifest; the identity pick dedupes them.
{{ config(materialized='ephemeral', tags=['gold', 'cadence:hourly', 'scope:global']) }}
select * from ({{ mdp_enrichment_rows('track_isrc_crosswalk') }}) rows_visible
