-- Exact resolution in SQL: no function call. It reads int_reference__current by key, never
-- through an ephemeral copy, which Postgres would materialize whole for each reference join.
{{ config(materialized='ephemeral', tags=['cadence:hourly', 'scope:global']) }}
{{ mdp_track_exact(ref('int_identity__track_inputs'), ref('int_reference__current')) }}
