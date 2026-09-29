-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", count(*) as n
from {{ source('raw', 'mb_resolve') }}
where _source_key = 'mb_resolve'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version"
having count(*) > 1
