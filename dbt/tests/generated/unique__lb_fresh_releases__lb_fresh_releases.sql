-- Generated physical uniqueness per dump and declaring source.
select "_dump_id", "release_mbid", "observed_at", count(*) as n
from {{ source('raw', 'lb_fresh_releases') }}
where _source_key = 'lb_fresh_releases'
group by "_dump_id", "release_mbid", "observed_at"
having count(*) > 1
