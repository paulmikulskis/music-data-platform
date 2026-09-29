-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "chart", "chart_date", "position", count(*) as n
from {{ source('raw', 'shazam_chart_entries') }}
where _source_key = 'sz_chart'
group by "_dump_id", "chart", "chart_date", "position"
having count(*) > 1
