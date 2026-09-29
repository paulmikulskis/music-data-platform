-- These private helpers read only the cycle's locally captured inputs.
DROP TABLE IF EXISTS bt_playlist_reads;
CREATE TEMP TABLE bt_playlist_reads AS
SELECT replace(platform,'apple_music','apple') AS platform,playlist_id,variant,stream,
    md5(platform || ':' || playlist_id || ':' || variant || ':' || stream) AS target,
    snapshot_id,observed_at,observed_at::date AS day,
    effective_coverage='full' AND (stream='full' OR extent_valid) AS complete
FROM {s}.int_playlist__snapshots;

DROP TABLE IF EXISTS bt_playlist_members;
CREATE TEMP TABLE bt_playlist_members AS
SELECT DISTINCT r.target,r.snapshot_id,r.observed_at,k.song_key
FROM {s}.int_playlist__observations o JOIN bt_playlist_reads r
    ON r.platform=replace(o.platform,'apple_music','apple') AND r.playlist_id=o.playlist_id
    AND r.variant=o.variant AND r.stream=o.stream AND r.snapshot_id=o.snapshot_id
JOIN {s}.int_song_key__daily k
    ON k.platform=r.platform AND k.platform_track_id=o.platform_track_id;
CREATE INDEX ON bt_playlist_members(target,snapshot_id,song_key);

DROP TABLE IF EXISTS bt_shazam_reads;
CREATE TEMP TABLE bt_shazam_reads AS
SELECT chart,chart_date,observed_at,_dump_id,
    count(*)=count(DISTINCT position) AND min(position)=1
    AND count(*)=CASE split_part(chart,':',2) WHEN 'top-200' THEN 200 WHEN 'top-50' THEN 50 END
    AND max(position)=count(*) AS complete
FROM raw.shazam_chart_entries
GROUP BY chart,chart_date,observed_at,_dump_id;

DROP TABLE IF EXISTS bt_shazam_members;
CREATE TEMP TABLE bt_shazam_members AS
SELECT DISTINCT z.chart,z.chart_date,z.observed_at,z._dump_id,k.song_key
FROM raw.shazam_chart_entries z JOIN {s}.int_song_key__daily k
    ON k.platform='apple' AND k.platform_track_id=z.apple_song_id;
CREATE INDEX ON bt_shazam_members(chart,chart_date,observed_at,_dump_id,song_key);

DROP TABLE IF EXISTS bt_stream_reads;
CREATE TEMP TABLE bt_stream_reads AS
WITH counters AS (
    SELECT s.*,k.song_key,
        lag(day) OVER (PARTITION BY s.platform,s.platform_track_id ORDER BY day) AS previous_day,
        lag(count_status) OVER (PARTITION BY s.platform,s.platform_track_id ORDER BY day) AS previous_status,
        max(CASE WHEN count_status IN ('no_fetch','no_count') THEN day END) OVER (
            PARTITION BY s.platform,s.platform_track_id ORDER BY day ROWS UNBOUNDED PRECEDING) AS last_gap_day
    FROM {s}.mart_track_daily_streams s JOIN {s}.int_song_key__daily k
        USING(platform,platform_track_id)
)
SELECT song_key,platform || ':' || platform_track_id AS target,day,
    CASE WHEN count_status='changed' AND streams_since_last_update>=0
        AND extract(epoch FROM (count_changed_at-previous_count_changed_at))/3600 BETWEEN 20 AND 28
        AND previous_day=day-1 AND previous_status IN ('changed','unchanged')
        AND (last_gap_day IS NULL OR last_gap_day<previous_count_changed_at::date)
        THEN streams_since_last_update END AS rate
FROM counters;

-- The target inventory includes unread frozen targets. A missing target cannot disappear from a denominator.
INSERT INTO backtest.targets
WITH playlists AS (
    SELECT DISTINCT platform,playlist_id,variant,stream,target FROM bt_playlist_reads
    UNION
    SELECT DISTINCT replace(platform,'apple_music','apple'),split_part(canonical_key,':',4),
        split_part(canonical_key,':',3),CASE WHEN platform='spotify' THEN 'head' ELSE 'full' END,
        md5(platform || ':' || split_part(canonical_key,':',4) || ':' || split_part(canonical_key,':',3)
            || ':' || CASE WHEN platform='spotify' THEN 'head' ELSE 'full' END)
    FROM raw.targets WHERE resource_kind='playlist' AND platform IN ('apple_music','spotify')
), charts AS (
    SELECT DISTINCT chart FROM bt_shazam_reads
    UNION SELECT DISTINCT 'shazam:' || (params_json->>'chart_type') || ':' || (params_json->>'country')
        || CASE WHEN coalesce(params_json->>'city','')<>'' THEN ':' || (params_json->>'city') ELSE '' END
    FROM raw.targets WHERE resource_kind='chart' AND platform='shazam'
)
SELECT DISTINCT %(cycle)s,%(method)s,'chart_market',p.target,NULL::text
FROM playlists p JOIN {s}.playlist_reach_tiers t
    ON replace(t.platform,'apple_music','apple')=p.platform AND t.playlist_id=p.playlist_id
WHERE t.list_kind='chart' AND length(t.market)=2
    AND (p.platform='apple' OR p.playlist_id LIKE '37i9dQZEVXb%%')
UNION SELECT DISTINCT %(cycle)s,%(method)s,'tier1_editorial',p.target,NULL::text
FROM playlists p JOIN {s}.playlist_reach_tiers t
    ON replace(t.platform,'apple_music','apple')=p.platform AND t.playlist_id=p.playlist_id
WHERE t.list_kind IN ('editorial','new_music') AND t.reach_tier=1
UNION SELECT %(cycle)s,%(method)s,'shazam_country',md5(chart),NULL::text FROM charts
UNION SELECT %(cycle)s,%(method)s,'shazam_city',md5(chart),NULL::text FROM charts
    WHERE nullif(split_part(chart,':',4),'') IS NOT NULL
UNION SELECT %(cycle)s,%(method)s,'stream_surge',platform || ':' || platform_track_id,song_key
FROM {s}.int_song_key__daily WHERE platform='spotify';

INSERT INTO backtest.observations
SELECT %(cycle)s,%(method)s,t.kind,r.target,r.day,bool_or(coalesce(r.complete,false))
FROM bt_playlist_reads r JOIN backtest.targets t ON t.target=r.target
    AND t.cycle_id=%(cycle)s AND t.method=%(method)s
GROUP BY t.kind,r.target,r.day
UNION ALL SELECT %(cycle)s,%(method)s,t.kind,md5(r.chart),r.chart_date,bool_or(coalesce(r.complete,false))
FROM bt_shazam_reads r JOIN backtest.targets t ON t.target=md5(r.chart)
    AND t.cycle_id=%(cycle)s AND t.method=%(method)s
GROUP BY t.kind,r.chart,r.chart_date
UNION ALL SELECT %(cycle)s,%(method)s,'stream_surge',target,day,rate IS NOT NULL FROM bt_stream_reads;
