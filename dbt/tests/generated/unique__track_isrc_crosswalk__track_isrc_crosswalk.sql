-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", count(*) as n
from {{ source('raw', 'track_isrc_crosswalk') }}
where _source_key = 'track_isrc_crosswalk'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version"
having count(*) > 1
