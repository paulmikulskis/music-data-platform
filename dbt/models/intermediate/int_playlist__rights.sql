{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}

-- Contributing source keys per playlist observation; each mart annotates them (mdp_annotate).
{{ mdp_playlist_source_keys(ref('int_playlist__snapshots'), ref('stg_playlist__snapshots'), ref('stg_playlist__items')) }}
