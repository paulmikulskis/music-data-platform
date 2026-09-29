-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "platform", "playlist_id", "variant", "stream", "snapshot_id", "position", count(*) as n
from {{ source('raw', 'playlist_snapshots') }}
where _source_key = 'sc_playlist_weekly'
group by "_dump_id", "platform", "playlist_id", "variant", "stream", "snapshot_id", "position"
having count(*) > 1
