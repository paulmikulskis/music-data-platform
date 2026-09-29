-- depends_on: {{ ref('bronze_close__hourly') }}
{#- design: the current MusicBrainz rows the hourly chain joins, keyed (mb_table, mb_key) with their
    tombstone state, from the newest generation that reconciles in this hourly cycle's manifest and the
    rows mb_resolve answers touched in it: ISRCs, URL relationships, recordings and merge redirects. The
    rest of the spine (tracks, releases, credits, artists) is read by mdp_reference_rows() where a model
    needs it. The spine is scoped to the tracked catalog, so the table is rebuilt every hour, and a
    resolution's rows join from the first hourly manifest that holds it. -#}
{{ config(materialized='table', tags=['cadence:hourly', 'scope:global'],
          post_hook=["{{ mdp_reference_indexes() }}", "{{ mdp_analyze() }}"]) }}
with generations as ({{ mdp_reference_generations() }})
select * from ({{ mdp_reference_current('generations', mdp_reference_hourly_tables()) }}) current_rows
