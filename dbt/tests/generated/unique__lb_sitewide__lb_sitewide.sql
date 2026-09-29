-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "entity_type", "window_start", "last_updated", "rank", count(*) as n
from {{ source('raw', 'lb_sitewide') }}
where _source_key = 'lb_sitewide'
group by "_dump_id", "entity_type", "window_start", "last_updated", "rank"
having count(*) > 1
