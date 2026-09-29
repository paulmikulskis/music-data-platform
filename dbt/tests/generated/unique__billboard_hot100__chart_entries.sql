-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "chart", "week", "position", count(*) as n
from {{ source('raw', 'chart_entries') }}
where _source_key = 'billboard_hot100'
group by "_dump_id", "chart", "week", "position"
having count(*) > 1
