-- A table: every playlist model and several per-row checks read this hub.
{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}

{{ mdp_playlist_snapshots(ref('stg_playlist__snapshots'), ref('stg_playlist__items')) }}
