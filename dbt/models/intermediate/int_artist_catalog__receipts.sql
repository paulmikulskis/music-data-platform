-- depends_on: {{ ref('gold_invoke__mb_artist_catalog') }}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

-- This cycle's mb_artist_catalog invoke receipt, for the operator: the invoke's dependent. Nothing reads it, so an
-- invoke that fails or runs long skips this model alone; the readings it landed reach int_song_age__daily at the
-- next cycle's close.
select cast('mb_artist_catalog' as text) as source_key, cast(run_id as text) as run_id, cast(status as text) as status,
    cast(coverage as text) as coverage, cast(rows_written as bigint) as rows_written,
    cast(rows_rejected as bigint) as rows_rejected, cast(message as text) as message
from {{ ref('gold_invoke__mb_artist_catalog') }}
