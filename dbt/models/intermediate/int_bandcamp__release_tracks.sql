{{ config(materialized='view', tags=['cadence:daily', 'scope:global']) }}

-- Ranked and listed Bandcamp releases related to their recordings: the tracks their
-- release pages list, and the separately identified featured track. A relation,
-- never a list membership: nothing here says a track was on a list.
with listed as (
    select distinct item_type as release_type, platform_item_id as release_id, featured_track_id
    from {{ ref('stg_playlist__items') }}
    where platform='bandcamp' and item_type in ('album','package')
), page_isrc as (
    select track_id, max(isrc) as isrc
    from {{ ref('stg_bandcamp__tracks') }}
    where page_item_type='track' and isrc is not null
    group by 1
), relations as (
    select cast('album' as text) as release_type, t.album_id as release_id, t.track_id,
        t.track_num, t.title, t.duration_ms, cast('release_page' as text) as method
    from {{ ref('stg_bandcamp__tracks') }} t
    where t.page_item_type='album' and t.album_id is not null
    union all
    select l.release_type, l.release_id, l.featured_track_id, null, null, null,
        cast('featured_track' as text)
    from listed l where l.featured_track_id is not null
)
select r.release_type, r.release_id, r.track_id,
    max(r.track_num) as track_num, max(r.title) as title, max(r.duration_ms) as duration_ms,
    max(i.isrc) as isrc,
    bool_or(r.method='featured_track') as is_featured,
    bool_or(r.method='release_page') as on_release_page
from relations r
left join page_isrc i on i.track_id=r.track_id
group by 1,2,3
