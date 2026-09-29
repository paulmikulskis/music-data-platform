-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "mb_generation", "mb_key", count(*) as n
from {{ source('raw', 'mb_track') }}
where _source_key = 'mb_spine'
group by "_dump_id", "mb_generation", "mb_key"
having count(*) > 1
