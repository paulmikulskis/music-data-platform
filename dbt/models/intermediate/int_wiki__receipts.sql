-- depends_on: {{ ref('gold_invoke__wiki_sitelinks') }}
-- depends_on: {{ ref('gold_invoke__wiki_pageviews') }}
{{ config(materialized='table', tags=['cadence:daily', 'scope:global']) }}

-- This cycle's wiki invoke receipts, for the operator: the invokes' dependent. Nothing reads it, so a
-- wiki invoke that fails or runs long skips this model alone; the wiki rows it landed reach
-- stg_wiki__pageviews at the next cycle's close.
select cast('wiki_sitelinks' as text) as source_key, cast(run_id as text) as run_id, cast(status as text) as status,
    cast(coverage as text) as coverage, cast(rows_written as bigint) as rows_written,
    cast(rows_rejected as bigint) as rows_rejected, cast(message as text) as message
from {{ ref('gold_invoke__wiki_sitelinks') }}
union all
select cast('wiki_pageviews' as text), cast(run_id as text), cast(status as text), cast(coverage as text),
    cast(rows_written as bigint), cast(rows_rejected as bigint), cast(message as text)
from {{ ref('gold_invoke__wiki_pageviews') }}
