-- A table: membership, events, and current membership all read it.
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}

{{ mdp_playlist_observations(ref('int_playlist__snapshots'), ref('stg_playlist__snapshots'), ref('stg_playlist__items')) }}
