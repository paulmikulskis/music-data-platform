-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "artist_id", count(*) as n
from {{ source('raw', 'sp_track_artists') }}
where _source_key = 'sp_track_artists'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "artist_id"
having count(*) > 1
