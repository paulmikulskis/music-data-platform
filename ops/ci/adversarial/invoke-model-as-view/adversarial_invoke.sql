{{ config(materialized='view', tags=['invoke','cadence:hourly','scope:global']) }}
{{ mdp_invoke('adversarial_source') }}
