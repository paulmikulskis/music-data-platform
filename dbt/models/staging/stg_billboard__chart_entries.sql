-- depends_on: {{ ref('bronze_close__weekly') }}
-- depends_on: {{ ref('bronze_invoke__billboard_hot100') }}
-- Scaffold. Staging = rename, type, dedupe. No business logic, no joins across sources.
-- Dedupe keeps the latest ingestion per (chart, week, position); functions append, never update.
-- Column names follow the billboard_hot100 function's declared schema (raw.chart_entries).
{{ config(materialized='table', tags=['cadence:weekly']) }}

{{ mdp_billboard_entries(source('raw', 'chart_entries')) }}
