-- Labels use the latest capture's identities. Feature tables are never updated.
DROP TABLE IF EXISTS backtest.outcome_keys;
CREATE TABLE backtest.outcome_keys AS
SELECT old.cycle_id,old.method,old.song_key,
    CASE WHEN count(DISTINCT now.song_key)=1 THEN min(now.song_key) END AS canonical_key
FROM backtest.aliases old
LEFT JOIN backtest.aliases now ON now.alias_key=old.alias_key
    AND now.cycle_id=%(cycle)s AND now.method=%(method)s
GROUP BY old.cycle_id,old.method,old.song_key;

DROP TABLE IF EXISTS backtest.events;
CREATE TABLE backtest.events AS
WITH facts AS (
    SELECT * FROM backtest.facts WHERE cycle_id=%(cycle)s AND method=%(method)s
), first_seen AS (
    SELECT song_key,kind,dimension,min(day) AS day
    FROM facts WHERE kind NOT IN ('stream_rate','tier1_editorial') GROUP BY song_key,kind,dimension
), entries AS (
    SELECT DISTINCT f.song_key,f.kind,f.dimension,f.day
    FROM facts f JOIN first_seen s USING(song_key,kind,dimension,day)
    WHERE f.candidate
    UNION SELECT DISTINCT song_key,kind,dimension,day FROM facts
    WHERE kind='tier1_editorial' AND candidate
), rates AS (
    SELECT song_key,day,max(value) AS value FROM facts WHERE kind='stream_rate'
    GROUP BY song_key,day
), sustained AS (
    -- Seven consecutive rated days before the surge, then three consecutive raised days.
    -- The outcome becomes known on day three. Missing rates and a zero baseline stay unknown.
    SELECT r.song_key,r.day + 2 AS day,'stream_surge' AS kind,'' AS dimension
    FROM rates r JOIN rates d2 ON d2.song_key=r.song_key AND d2.day=r.day+1
    JOIN rates d3 ON d3.song_key=r.song_key AND d3.day=r.day+2
    JOIN rates b ON b.song_key=r.song_key AND b.day BETWEEN r.day-7 AND r.day-1
    GROUP BY r.song_key,r.day,r.value,d2.value,d3.value
    HAVING count(*)=7 AND avg(b.value)>0 AND least(r.value,d2.value,d3.value)>=1.5*avg(b.value)
), first_surge AS (
    SELECT DISTINCT s.song_key,s.day,s.kind,s.dimension FROM sustained s
    WHERE NOT EXISTS (SELECT 1 FROM sustained prior WHERE prior.song_key=s.song_key AND prior.day=s.day-1)
), chart_artists AS (
    SELECT a.artist_key,min(f.day) AS day,
        min(CASE WHEN NOT f.candidate THEN f.day END) AS baseline_day
    FROM facts f JOIN backtest.artists a ON a.song_key=f.song_key
        AND a.cycle_id=%(cycle)s AND a.method=%(method)s
    WHERE f.kind IN ('shazam_country','shazam_city','chart_market')
    GROUP BY a.artist_key
), artist_entries AS (
    SELECT min(e.song_key) AS song_key,e.day,'artist_first_chart' AS kind,a.artist_key AS dimension
    FROM entries e JOIN backtest.artists a ON a.song_key=e.song_key
        AND a.cycle_id=%(cycle)s AND a.method=%(method)s
    JOIN chart_artists first ON first.artist_key=a.artist_key AND first.day=e.day
    WHERE e.kind IN ('shazam_country','shazam_city','chart_market')
        -- A same-day baseline can precede another chart's entry. Dates cannot order them.
        AND (first.baseline_day IS NULL OR first.baseline_day>e.day)
    GROUP BY e.day,a.artist_key
)
SELECT song_key,day,kind,dimension FROM entries
UNION SELECT song_key,day,kind,dimension FROM first_surge
UNION SELECT song_key,day,kind,dimension FROM artist_entries;
