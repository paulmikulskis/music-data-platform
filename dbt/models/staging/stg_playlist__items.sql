-- depends_on: {{ ref('bronze_close__daily') }}
-- A table: every playlist model joins it, so it is deduplicated once per build.
{{ config(materialized='table', tags=['cadence:daily', 'scope:global'], post_hook="{{ mdp_analyze() }}") }}

{{ mdp_playlist_item_rows(mdp_context().manifest_filter('_dump_id', 'raw.playlist_items')) }}
