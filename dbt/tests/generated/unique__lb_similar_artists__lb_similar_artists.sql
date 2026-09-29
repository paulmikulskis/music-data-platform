-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "neighbour_mbid", count(*) as n
from {{ source('raw', 'lb_similar_artists') }}
where _source_key = 'lb_similar_artists'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "neighbour_mbid"
having count(*) > 1
