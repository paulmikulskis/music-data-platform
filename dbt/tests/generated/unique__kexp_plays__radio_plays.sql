-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "station", "play_id", count(*) as n
from {{ source('raw', 'radio_plays') }}
where _source_key = 'kexp_plays'
group by "_dump_id", "station", "play_id"
having count(*) > 1
