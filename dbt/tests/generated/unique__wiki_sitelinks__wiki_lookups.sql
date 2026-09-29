-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "entity_qid", count(*) as n
from {{ source('raw', 'wiki_lookups') }}
where _source_key = 'wiki_sitelinks'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "entity_qid"
having count(*) > 1
