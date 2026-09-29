{#- design: one MusicBrainz artist per (platform, platform_artist_id) from the landed URL
    relationships, when exactly one artist carries the platform's artist URL; link-in-bio evidence joins
    in step 6. The artist's Wikidata QID comes from its own Wikidata URL relationship, when it
    has exactly one. -#}
{{ config(materialized='table', meta={'record_build': true}, tags=['cadence:daily', 'scope:global'], pre_hook="{{ mdp_hash_join_plan() }}") }}
with generations as ({{ mdp_reference_generations() }}),
reference as ({{ mdp_reference_current('generations', ['url_link', 'artist']) }}),
links as (
    select url_platform as platform, url_platform_id as platform_artist_id,
        count(distinct entity_id) as candidates, min(entity_id) as artist_id, min(url) as url
    from reference
    where mb_table = 'url_link' and entity_type = 'artist' and url_kind = 'artist' and not tombstoned
    group by 1, 2
),
wikidata as (
    select entity_id as artist_id, count(distinct url_platform_id) as qids, min(url_platform_id) as qid
    from reference
    where mb_table = 'url_link' and entity_type = 'artist' and url_platform = 'wikidata' and not tombstoned
    group by 1
)
select l.platform, l.platform_artist_id, l.candidates as candidate_count,
    case when l.candidates = 1 then a.artist_gid end as mb_artist_gid,
    case when l.candidates = 1 then a.name end as mb_artist_name,
    case when l.candidates = 1 then 'mb_url' end as method, l.url as evidence,
    case when l.candidates = 1 and w.qids = 1 then w.qid end as wikidata_qid
from links l
left join reference a on a.mb_table = 'artist' and a.artist_id = l.artist_id and not a.tombstoned
left join wikidata w on w.artist_id = l.artist_id
