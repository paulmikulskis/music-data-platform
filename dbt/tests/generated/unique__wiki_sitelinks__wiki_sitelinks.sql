-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "site", count(*) as n
from {{ source('raw', 'wiki_sitelinks') }}
where _source_key = 'wiki_sitelinks'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "site"
having count(*) > 1
