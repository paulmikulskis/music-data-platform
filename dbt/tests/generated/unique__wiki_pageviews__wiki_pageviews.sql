-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "date", count(*) as n
from {{ source('raw', 'wiki_pageviews') }}
where _source_key = 'wiki_pageviews'
group by "_dump_id", "_source_key", "scope", "input_ref", "input_version", "step", "config_version", "date"
having count(*) > 1
