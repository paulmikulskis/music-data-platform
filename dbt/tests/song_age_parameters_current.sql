{{ config(tags=['cadence:daily', 'scope:global']) }}
-- depends_on: {{ ref('int_song_age__daily') }}
-- Remeasure the Apple bands and review the ISRC rule in seeds/song_age_parameters.csv, then run dbt seed.
select name, measured_on
from {{ ref('song_age_parameters') }}
where extract(year from {{ mdp_cycle_day() }}) > extract(year from measured_on)
