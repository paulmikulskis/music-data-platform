-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "primary_type", "secondary_types", count(*) as n
from {{ source('raw', 'mb_artist_release_groups') }}
where _source_key = 'mb_artist_catalog'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "primary_type", "secondary_types"
having count(*) > 1
