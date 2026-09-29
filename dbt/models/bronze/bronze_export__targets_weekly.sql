{{ config(materialized='table', tags=['bronze', 'export', 'cadence:weekly', 'scope:global']) }}
{{ mdp_invoke('targets_export', target_kinds=mdp_export_kinds('weekly', 'global')) }}
