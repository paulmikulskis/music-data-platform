-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "mb_table", "mb_key", count(*) as n
from {{ source('raw', 'mb_resolve_closure') }}
where _source_key = 'mb_resolve'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "mb_table", "mb_key"
having count(*) > 1
