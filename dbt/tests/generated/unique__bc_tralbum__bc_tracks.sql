-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "page_url", "position", count(*) as n
from {{ source('raw', 'bc_tracks') }}
where _source_key = 'bc_tralbum'
group by "_dump_id", "page_url", "position"
having count(*) > 1
