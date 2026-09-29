{{ config(materialized='table', tags=['bronze', 'export', 'cadence:__CADENCE__', 'scope:__SCOPE__']) }}
{{ mdp_invoke('targets_export', target_kinds=mdp_export_kinds('__CADENCE__', '__SCOPE__')) }}
