-- depends_on: {{ ref('gold_invoke__mb_resolve') }}
-- The rows visible in this cycle's manifest; the identity pick dedupes them.
{{ config(materialized='ephemeral', tags=['gold', 'cadence:hourly', 'scope:global']) }}
select * from ({{ mdp_enrichment_rows('mb_resolve') }}) rows_visible
