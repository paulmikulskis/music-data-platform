-- Synthetic week questions. Run as analyst_local after bash ops/local/up.sh.
-- A movement group counts each city chart once, across its Apple and Spotify copies.
WITH copies AS (
  SELECT k.platform_track_id, m.cluster_key
  FROM intermediate.int_song_key__daily k
  JOIN marts.mart_song_cluster_members m USING (song_key)
  WHERE k.platform = 'apple'
), latest AS (SELECT max(chart_date) AS day FROM marts.mart_shazam_chart_daily)
SELECT c.cluster_key, min(s.title_text) AS title,
  count(DISTINCT s.chart) AS city_charts,
  bool_and(s.learning_eligible) AS learning_eligible,
  bool_and(s.resale_permitted) AS resale_permitted
FROM marts.mart_shazam_chart_daily s JOIN copies c ON c.platform_track_id = s.apple_song_id
CROSS JOIN latest
WHERE s.chart_date BETWEEN latest.day - 6 AND latest.day AND nullif(s.city, '') IS NOT NULL
GROUP BY c.cluster_key ORDER BY city_charts DESC, title;

-- Playlist additions by emerging artists. Baselines do not establish an add.
SELECT e.platform, e.playlist_id, count(DISTINCT k.song_key) AS songs_added,
  bool_and(e.learning_eligible) AS learning_eligible, bool_and(e.resale_permitted) AS resale_permitted
FROM marts.mart_playlist_events e
JOIN intermediate.int_song_key__daily k
  ON k.platform = CASE WHEN e.platform = 'apple_music' THEN 'apple' ELSE e.platform END
  AND k.platform_track_id = e.platform_track_id
JOIN intermediate.int_song_age__daily a USING (song_key)
WHERE e.event_type = 'add' AND a.artist_stage = 'emerging'
GROUP BY e.platform, e.playlist_id ORDER BY songs_added DESC, e.platform, e.playlist_id;

-- A missing match stays unkeyed. It does not vanish from the chart.
SELECT chart_week, count(*) AS entries, count(song_key) AS keyed,
  count(*) FILTER (WHERE song_key IS NULL) AS unkeyed,
  bool_and(learning_eligible) AS learning_eligible, bool_and(resale_permitted) AS resale_permitted
FROM marts.mart_chart_history WHERE chart_name = 'hot-100'
GROUP BY chart_week ORDER BY chart_week DESC;
