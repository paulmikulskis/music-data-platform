{{ config(tags=['cadence:daily'], post_hook=['{{ mdp_search_index() }}', '{{ mdp_analyze() }}']) }}
with observed as (
    -- Presence, events and real counters qualify. A collected day with zero presence does not.
    select song_key, cast(day as timestamp) as last_seen, source_keys
    from {{ ref('mart_song_day') }}
    where list_count > 0 or shazam_best_position is not null or streams_observed
        or billboard_position is not null or editorial_adds > 0 or algorithmic_adds > 0 or removes > 0
    union all
    select k.song_key, e.observed_at, e.source_keys
    from {{ ref('mart_playlist_events') }} e
    join {{ ref('int_song_key__daily') }} k
        on k.platform = {{ mdp_song_platform('e.platform') }} and k.platform_track_id = e.platform_track_id
), recent as (
    select * from observed
    where cast(last_seen as date) between {{ mdp_cycle_day() }} - 89 and {{ mdp_cycle_day() }}
), seen as (
    select song_key, max(last_seen) as last_seen from recent group by 1
), copies as (
    select k.*, coalesce(m.representative_song_key, k.song_key) as representative,
        s.last_seen
    from {{ ref('int_song_key__daily') }} k
    left join {{ ref('mart_song_cluster_members') }} m using (song_key)
    left join seen s using (song_key)
), song_names as (
    select *, row_number() over (partition by representative order by
        case when song_key = representative then 0 else 1 end, last_seen desc nulls last,
        platform, platform_track_id) as pick
    from copies
), song_seen as (
    select representative, max(last_seen) as last_seen from copies group by 1
    having max(last_seen) is not null
), song_alias_rows as (
    select representative, song_key as alias from copies
    union select representative, platform || ':' || platform_track_id from copies
    union select c.representative, a.alias_key
    from copies c join {{ ref('mart_song_aliases') }} a on a.song_key = c.song_key
), song_aliases as (
    select representative, {{ mdp_json_agg('alias', 'alias') }} as aliases
    from song_alias_rows group by 1
), song_rights as (
    select c.representative, {{ mdp_source_keys_agg('r.value') }} as source_keys
    from copies c {{ mdp_json_elements('c.source_keys', 'r') }} group by 1
), songs as (
    select 'song:' || n.representative as object_key, 'song' as kind,
        coalesce(nullif(n.title_text, ''), 'Untitled song') as display_text,
        cast({{ mdp_json_object([('key', 'n.representative'), ('subtitle', 'n.artist_text'), ('art_song', 'n.representative')]) }} as text) as context,
        a.aliases, s.last_seen, r.source_keys
    from song_names n join song_seen s using (representative)
    join song_aliases a using (representative) left join song_rights r using (representative)
    where n.pick = 1
), credited as (
    {{ mdp_search_artists() }}
), source_artists as (
    select c.platform, c.primary_artist_id as artist_id, c.artist_text as artist_name,
        c.last_seen, c.source_keys
    from copies c where c.last_seen is not null and c.primary_artist_id is not null
    union all
    select c.platform, a.artist_id, a.artist_name, c.last_seen, c.source_keys
    from credited a join copies c on c.platform = a.platform and c.platform_track_id = a.platform_track_id
    where c.last_seen is not null and a.artist_id is not null
), artist_keys as (
    select coalesce(a.mb_artist_gid, c.platform || ':' || c.artist_id) as artist_key,
        c.platform, c.artist_id as primary_artist_id,
        coalesce(a.mb_artist_name, c.artist_name, 'Artist') as display_text,
        a.wikidata_qid, c.last_seen, c.source_keys
    from source_artists c left join {{ ref('int_artist_identity') }} a
        on a.candidate_count = 1 and {{ mdp_song_platform('a.platform') }} = c.platform
        and a.platform_artist_id = c.artist_id
), artist_rows as (
    select *, row_number() over (partition by artist_key order by last_seen desc,
        platform, primary_artist_id, display_text) as pick from artist_keys
), artist_alias_rows as (
    select distinct artist_key, platform || ':' || primary_artist_id as alias from artist_rows
), artist_aliases as (
    select artist_key, {{ mdp_json_agg('alias', 'alias') }} as aliases from artist_alias_rows group by 1
), artist_rights as (
    select artist_key, {{ mdp_source_keys_agg('r.value') }} as source_keys
    from artist_rows {{ mdp_json_elements('source_keys', 'r') }} group by 1
), artists as (
    select 'artist:' || a.artist_key, 'artist', a.display_text,
        cast({{ mdp_json_object([('key', 'a.artist_key'), ('platform', 'a.platform'), ('artist_id', 'a.primary_artist_id'), ('wikidata_qid', 'a.wikidata_qid')]) }} as text),
        aliases.aliases, a.last_seen, r.source_keys
    from artist_rows a join artist_aliases aliases using (artist_key)
    join artist_rights r using (artist_key) where a.pick = 1
), playlists as (
    select *, row_number() over (partition by platform, playlist_id order by observed_at desc, variant, stream) as pick
    from {{ ref('mart_playlist_profile') }}
), chart_rows as (
    select chart, chart_type, country, city, chart_date as last_seen, source_keys,
        row_number() over (partition by chart order by chart_date desc, position) as pick
    from {{ ref('mart_shazam_chart_daily') }}
), objects as (
    select * from songs union all select * from artists
    union all
    select 'playlist:' || platform || ':' || playlist_id, 'playlist', coalesce(nullif(title, ''), 'Playlist'),
        cast({{ mdp_json_object([('key', "platform || ':' || playlist_id"), ('platform', 'platform'), ('playlist_id', 'playlist_id')]) }} as text),
        '[]', cast(observed_at as timestamp), source_keys from playlists where pick = 1
    union all
    select 'chart:' || chart, 'chart', coalesce(nullif(city, ''), nullif(country, ''), 'Global') || ' Shazam ' || coalesce(chart_type, 'chart'),
        cast({{ mdp_json_object([('key', 'chart'), ('country', 'country'), ('city', 'city')]) }} as text),
        '[]', cast(last_seen as timestamp), source_keys from chart_rows where pick = 1
    union all
    select 'chart:billboard:' || chart_name, 'chart', 'Billboard Hot 100',
        cast({{ mdp_json_object([('key', "'billboard:' || chart_name")]) }} as text),
        '[]', cast(max(chart_week) as timestamp), max(source_keys)
    from {{ ref('mart_chart_history') }} group by chart_name
), rows as (
    select cast(object_key as text) as object_key, cast(kind as text) as kind,
        cast(display_text as text) as display_text, cast(context as text) as context,
        cast(aliases as text) as aliases, cast(last_seen as timestamp) as last_seen,
        source_keys as _source_keys
    from objects
    where cast(last_seen as date) between {{ mdp_cycle_day() }} - 89 and {{ mdp_cycle_day() }}
)
{{ mdp_annotate('rows') }}
