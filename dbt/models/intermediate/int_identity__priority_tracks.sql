{{ config(materialized='ephemeral', tags=['cadence:hourly', 'scope:global']) }}
-- Public editorial, algorithmic and chart observations prioritize identity resolution.
{{ mdp_priority_tracks(ref('int_identity__track_inputs')) }}
