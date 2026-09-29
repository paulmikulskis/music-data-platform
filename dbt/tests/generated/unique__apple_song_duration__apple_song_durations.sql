-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "apple_song_id", "observed_at", count(*) as n
from {{ source('raw', 'apple_song_durations') }}
where _source_key = 'apple_song_duration'
group by "_dump_id", "apple_song_id", "observed_at"
having count(*) > 1
