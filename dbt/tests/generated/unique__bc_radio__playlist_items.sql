-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "platform", "playlist_id", "variant", "stream", "snapshot_id", "position", count(*) as n
from {{ source('raw', 'playlist_items') }}
where _source_key = 'bc_radio'
group by "_dump_id", "platform", "playlist_id", "variant", "stream", "snapshot_id", "position"
having count(*) > 1
