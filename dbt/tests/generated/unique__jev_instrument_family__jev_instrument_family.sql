-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "question", count(*) as n
from {{ source('raw', 'jev_instrument_family') }}
where _source_key = 'jev_instrument_family'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "question"
having count(*) > 1
