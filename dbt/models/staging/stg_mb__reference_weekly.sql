-- depends_on: {{ ref('universal_invoke__mb_spine') }}
-- depends_on: {{ ref('bronze_close__weekly') }}
-- The weekly copy of reference staging: the current MusicBrainz rows of the newest generation that
-- reconciles in the weekly cycle's manifest. Weekly models inline it; nothing is materialized.
{{ config(materialized='ephemeral', tags=['cadence:weekly', 'scope:global']) }}
with generations as ({{ mdp_reference_generations() }})
select * from ({{ mdp_reference_current('generations') }}) current_rows
