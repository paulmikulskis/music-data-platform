-- depends_on: {{ ref('bronze_invoke__am_playlist') }}
-- depends_on: {{ ref('bronze_invoke__sp_playlist') }}
-- depends_on: {{ ref('bronze_invoke__sc_playlist') }}
-- depends_on: {{ ref('bronze_invoke__sc_curator_playlists') }}
-- depends_on: {{ ref('bronze_invoke__sc_curator_playlists_weekly') }}
-- depends_on: {{ ref('bronze_invoke__bc_discover') }}
-- depends_on: {{ ref('bronze_invoke__bc_daily_list') }}
-- depends_on: {{ ref('bronze_invoke__bc_radio') }}
-- depends_on: {{ ref('bronze_invoke__bc_fan_playlist') }}
-- depends_on: {{ ref('bronze_invoke__am_playlist_weekly') }}
-- depends_on: {{ ref('bronze_invoke__sp_playlist_weekly') }}
-- depends_on: {{ ref('bronze_invoke__sc_playlist_weekly') }}
-- depends_on: {{ ref('bronze_invoke__bc_discover_weekly') }}
-- depends_on: {{ ref('bronze_invoke__bc_daily_list_weekly') }}
-- depends_on: {{ ref('bronze_invoke__bc_radio_weekly') }}
-- depends_on: {{ ref('bronze_invoke__bc_fan_playlist_weekly') }}
-- depends_on: {{ ref('bronze_close__daily') }}
-- `_weekly` sources run daily and fetch the weekly targets due today.
-- A table: every playlist model joins it, so it is reconciled once per build.
{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}", post_hook=["{{ mdp_analyze() }}", "{{ mdp_record_build() }}"]) }}

{{ mdp_playlist_snapshot_rows(mdp_context().manifest_filter('_dump_id', 'raw.playlist_snapshots'), ref('stg_playlist__items')) }}
