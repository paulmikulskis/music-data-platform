-- A table: events, current membership, and later reach all join it.
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}

{{ mdp_playlist_membership(ref('int_playlist__observations'), ref('int_playlist__snapshots')) }}
