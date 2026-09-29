"""SQL projections keep private text out of measurement tables and CSVs."""

from pathlib import Path

from mdp_functions.name_folding import sql as fold_sql
from psycopg import sql


def negative_title_sql(expression):
    """Broaden only absence blockers; a shortened title never proves a positive."""
    folded = fold_sql(expression)
    # Any trailing version or credit may hide the same song. Overblocking keeps
    # an uncertain negative pending, including nested or unfinished suffixes.
    return (
        "regexp_replace(regexp_replace(regexp_replace("
        + folded
        + r", '[[:space:]]*[(\[].*$', ''), "
        + r"'(^|[[:space:][:punct:]])(featuring|feat|ft|with|x)([[:space:][:punct:]]+).*$',''),'[[:space:][:punct:]]+$','')"
    )


def initialize(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest.billboard_weeks (
            cycle_id text, method text, day date, entries integer, keyed integer,
            PRIMARY KEY(cycle_id,method,day));
        CREATE TABLE IF NOT EXISTS backtest.billboard_blockers (
            cycle_id text, method text, song_key text, day date, reason text,
            PRIMARY KEY(cycle_id,method,song_key,day,reason));
        CREATE TABLE IF NOT EXISTS backtest.family_audit (
            cycle_id text, method text, song_key text, before_families integer,
            after_families integer, before_followers double precision, after_followers double precision,
            before_charts double precision, after_charts double precision,
            PRIMARY KEY(cycle_id,method,song_key));
        CREATE TABLE IF NOT EXISTS backtest.billboard_members (
            cycle_id text, method text, song_key text, cluster_key text,
            PRIMARY KEY(cycle_id,method,song_key));
        CREATE TABLE IF NOT EXISTS backtest.builds (
            cycle_id text, method text, day date, close_no bigint, sha text,
            songs bigint, two_families bigint, arrivals bigint,
            PRIMARY KEY (cycle_id,method));
        CREATE TABLE IF NOT EXISTS backtest.failures (
            cycle_id text, method text, error_class text, reference_id text,
            PRIMARY KEY(cycle_id,method));
        CREATE TABLE IF NOT EXISTS backtest.inputs (
            cycle_id text, method text, capture_hash text, harness_hash text, PRIMARY KEY(cycle_id,method));
        CREATE TABLE IF NOT EXISTS backtest.pool (
            cycle_id text, method text, day date, song_key text, movement_list text,
            families integer, playlist_adds double precision, follower_exposure_gain double precision,
            shazam_spread_gain double precision, stream_rate_gain double precision,
            chart_spread_gain double precision, playlist_followers double precision,
            shazam_charts double precision, PRIMARY KEY(cycle_id,method,song_key));
        CREATE TABLE IF NOT EXISTS backtest.predictions (
            cycle_id text, method text, day date, song_key text, list text, movement_list text,
            rank bigint, score double precision, playlist_adds double precision,
            follower_exposure_gain double precision, shazam_spread_gain double precision,
            stream_rate_gain double precision, chart_spread_gain double precision, families integer,
            PRIMARY KEY(cycle_id,method,song_key,list,movement_list));
        CREATE TABLE IF NOT EXISTS backtest.aliases (
            cycle_id text, method text, alias_key text, song_key text,
            PRIMARY KEY(cycle_id,method,alias_key));
        CREATE TABLE IF NOT EXISTS backtest.artists (
            cycle_id text, method text, song_key text, artist_key text,
            PRIMARY KEY(cycle_id,method,song_key,artist_key));
        CREATE TABLE IF NOT EXISTS backtest.facts (
            cycle_id text, method text, song_key text, day date,
            kind text, dimension text, candidate boolean, value double precision);
        CREATE TABLE IF NOT EXISTS backtest.readiness (
            cycle_id text, method text, family text, day date, history_days bigint, first_rank_day date,
            PRIMARY KEY(cycle_id,method,family));
        CREATE TABLE IF NOT EXISTS backtest.targets (
            cycle_id text, method text, kind text, target text, song_key text);
        CREATE TABLE IF NOT EXISTS backtest.observations (
            cycle_id text, method text, kind text, target text, day date, complete boolean,
            PRIMARY KEY(cycle_id,method,kind,target,day));
    """)


def extract(conn, method, cycle):
    schema = sql.Identifier(method["schema"])
    day = cycle["opened_at"][:10]
    prefix = (cycle["id"], method["name"])

    def execute(query, params=()):
        return conn.execute(sql.SQL(query).format(s=schema), params)

    execute(
        """
        INSERT INTO backtest.pool
        WITH members AS (
            SELECT DISTINCT song_key,cluster_key FROM {s}.mart_song_cluster_members
        ), places AS (
            SELECT f.day,m.cluster_key AS song_key,f.platform,f.playlist_id,max(f.followers) AS followers
            FROM {s}.int_song_followers__daily f JOIN members m USING(song_key)
            GROUP BY f.day,m.cluster_key,f.platform,f.playlist_id
        ), playlists AS (
            SELECT day,song_key,count(*) AS list_count,sum(followers) AS playlist_followers
            FROM places GROUP BY day,song_key
        ), charts AS (
            SELECT z.chart_date AS day,m.cluster_key AS song_key,count(DISTINCT z.chart) AS shazam_charts
            FROM {s}.int_song_shazam__daily z JOIN members m USING(song_key)
            GROUP BY z.chart_date,m.cluster_key
        ), streams AS (
            SELECT d.day,m.cluster_key AS song_key,bool_or(d.streams_observed) AS streams_observed,
                bool_or(d.playlist_followers IS NOT NULL) AS followers_known,
                bool_or(d.shazam_charts IS NOT NULL) AS charts_known
            FROM {s}.mart_song_day d JOIN members m USING(song_key)
            GROUP BY d.day,m.cluster_key
        ), grouped AS (
            SELECT d.day,d.song_key,d.streams_observed,coalesce(p.list_count,0) AS list_count,
                CASE WHEN p.list_count IS NULL AND d.followers_known THEN 0
                    ELSE p.playlist_followers END AS playlist_followers,
                CASE WHEN d.charts_known THEN coalesce(z.shazam_charts,0)
                    ELSE z.shazam_charts END AS shazam_charts
            FROM streams d LEFT JOIN playlists p USING(day,song_key)
            LEFT JOIN charts z USING(day,song_key)
        )
        SELECT %s,%s,q.day,q.song_key,q.movement_list,
            (coalesce(d.list_count,0)>0)::int + (coalesce(d.shazam_charts,0)>0)::int
                + d.streams_observed::int,
            q.playlist_adds,q.follower_exposure_gain,q.shazam_spread_gain,q.stream_rate_gain,
            q.chart_spread_gain,d.playlist_followers,d.shazam_charts
        FROM {s}.int_song_movement__daily q JOIN grouped d USING(day,song_key)
        WHERE q.day=%s
    """,
        (*prefix, day),
    )
    execute(
        """
        INSERT INTO backtest.family_audit
        SELECT p.cycle_id,p.method,p.song_key,
            (coalesce(d.list_count,0)>0)::int + (coalesce(d.shazam_charts,0)>0)::int
                + coalesce(d.streams_observed,false)::int,
            p.families,d.playlist_followers,p.playlist_followers,d.shazam_charts,p.shazam_charts
        FROM backtest.pool p LEFT JOIN {s}.mart_song_day d USING(day,song_key)
        WHERE p.cycle_id=%s AND p.method=%s
        """,
        prefix,
    )
    # Keep the lists separate: ranks restart within each Rising/Places split.
    for relation, list_sql, score in (
        ("mart_top_movers", "'movers'", "momentum_score"),
        ("mart_early_signals_current", "'early_' || family", "value"),
        ("mart_arrivals_current", "'arrivals'", "NULL::double precision"),
    ):
        query = sql.SQL("""
            INSERT INTO backtest.predictions
            SELECT p.cycle_id,p.method,p.day,p.song_key,{list},p.movement_list,q.rank,{score},
                p.playlist_adds,p.follower_exposure_gain,p.shazam_spread_gain,p.stream_rate_gain,
                p.chart_spread_gain,p.families
            FROM {schema}.{relation} q JOIN backtest.pool p USING(day,song_key,movement_list)
            WHERE p.cycle_id=%s AND p.method=%s
        """).format(
            list=sql.SQL(list_sql),
            score=sql.SQL(score),
            schema=schema,
            relation=sql.Identifier(relation),
        )
        conn.execute(query, prefix)
    # Every baseline sees exactly the same pool at D. No outcome or future alias enters its order.
    for name, value in (
        ("random", "NULL::double precision"),
        ("size_followers", "playlist_followers"),
        ("size_charts", "shazam_charts"),
        (
            "family_playlists",
            "coalesce(nullif(playlist_adds,0),follower_exposure_gain)",
        ),
        ("family_shazam", "shazam_spread_gain"),
        ("family_streams", "stream_rate_gain"),
    ):
        order = "md5('mdp-backtest-v1:' || day::text || song_key)"
        if name != "random":
            order = value + " DESC NULLS LAST," + order
        if name == "family_playlists":
            order = "(coalesce(playlist_adds,0)>0) DESC," + order
        condition = "families>0"
        if name.startswith("family_"):
            condition += " AND " + value + ">0"
        elif name.startswith("size_"):
            condition += " AND " + value + " IS NOT NULL"
        conn.execute(
            sql.SQL("""
            WITH ranked AS (
            SELECT cycle_id,method,day,song_key,%s,movement_list,
                row_number() OVER(PARTITION BY movement_list ORDER BY {order}) AS rank,{value},
                playlist_adds,follower_exposure_gain,shazam_spread_gain,stream_rate_gain,chart_spread_gain,families
            FROM backtest.pool WHERE cycle_id=%s AND method=%s AND {condition}
            ) INSERT INTO backtest.predictions SELECT * FROM ranked WHERE rank<=50
        """).format(
                order=sql.SQL(order), value=sql.SQL(value), condition=sql.SQL(condition)
            ),
            (name, *prefix),
        )
    execute(
        "INSERT INTO backtest.aliases SELECT %s,%s,alias_key,song_key FROM {s}.mart_song_aliases",
        prefix,
    )
    execute(
        """
        INSERT INTO backtest.artists
        SELECT DISTINCT %s,%s,song_key,primary_artist_key FROM {s}.int_song_key__daily
        WHERE primary_artist_key IS NOT NULL
    """,
        prefix,
    )
    for statement in (
        (Path(__file__).parent / "observations.sql").read_text().split(";")
    ):
        if statement.strip():
            execute(statement, {"cycle": cycle["id"], "method": method["name"]})
    # Presence history is kept alongside bounded entries. A first collection is not a debut.
    execute(
        """
        INSERT INTO backtest.facts
        SELECT %s,%s,z.song_key,z.chart_date,v.kind,v.dimension,
            EXISTS (SELECT 1 FROM bt_shazam_reads current
                JOIN bt_shazam_reads prior ON prior.chart=current.chart
                    AND prior.chart_date<current.chart_date AND prior.observed_at<current.observed_at
                WHERE current.chart=z.chart AND current.chart_date=z.chart_date
                    AND current.complete AND prior.complete
                    AND NOT EXISTS (SELECT 1 FROM bt_shazam_members m
                        WHERE m.chart=prior.chart AND m.chart_date=prior.chart_date
                            AND m.observed_at=prior.observed_at AND m._dump_id=prior._dump_id
                            AND m.song_key=z.song_key)),NULL
        FROM {s}.int_song_shazam__daily z
        CROSS JOIN LATERAL (VALUES
            ('shazam_country',md5(upper(nullif(z.country,'')))),
            ('shazam_city',CASE WHEN nullif(z.city,'') IS NOT NULL
                THEN md5(upper(coalesce(z.country,'')) || ':' || lower(z.city)) END)
        ) v(kind,dimension) WHERE v.dimension IS NOT NULL
    """,
        prefix,
    )
    execute(
        """
        INSERT INTO backtest.facts
        SELECT %s,%s,f.song_key,f.day,'chart_market',md5(f.platform || ':' || upper(t.market)),
            EXISTS (SELECT 1 FROM {s}.int_song_entries__daily e
                JOIN bt_playlist_reads current ON current.platform=e.platform
                    AND current.playlist_id=e.list_id AND current.snapshot_id=e.snapshot_id
                JOIN bt_playlist_reads prior ON prior.target=current.target
                    AND prior.observed_at<current.observed_at
                WHERE e.song_key=f.song_key AND e.list_id=f.playlist_id
                    AND e.platform=f.platform AND e.day=f.day AND e.event_type='add'
                    AND current.complete AND prior.complete
                    AND NOT EXISTS (SELECT 1 FROM bt_playlist_members m
                        WHERE m.target=prior.target AND m.snapshot_id=prior.snapshot_id
                            AND m.song_key=f.song_key)),NULL
        FROM {s}.int_song_followers__daily f JOIN {s}.playlist_reach_tiers t
            ON replace(t.platform,'apple_music','apple')=f.platform AND t.playlist_id=f.playlist_id
        WHERE t.list_kind='chart' AND length(t.market)=2
            AND (f.platform='apple' OR f.playlist_id LIKE '37i9dQZEVXb%%')
    """,
        prefix,
    )
    execute(
        """
        INSERT INTO backtest.facts
        SELECT %s,%s,e.song_key,e.day,'tier1_editorial',md5(e.platform || ':' || e.list_id),
            e.event_type='add',NULL::double precision
        FROM {s}.int_song_entries__daily e
        WHERE e.list_kind IN ('editorial','new_music') AND e.list_reach_tier=1
        UNION ALL
        SELECT %s,%s,f.song_key,f.day,'tier1_editorial',md5(f.platform || ':' || f.playlist_id),false,NULL::double precision
        FROM {s}.int_song_followers__daily f JOIN {s}.playlist_reach_tiers t
            ON replace(t.platform,'apple_music','apple')=f.platform AND t.playlist_id=f.playlist_id
        WHERE t.list_kind IN ('editorial','new_music') AND t.reach_tier=1
    """,
        (*prefix, *prefix),
    )
    execute(
        """
        INSERT INTO backtest.facts
        SELECT %s,%s,r.song_key,r.day,'stream_rate','',false,sum(r.rate)
        FROM bt_stream_reads r GROUP BY r.song_key,r.day
        HAVING bool_and(r.rate IS NOT NULL) AND count(*)=(
            SELECT count(*) FROM {s}.int_song_key__daily k
            WHERE k.song_key=r.song_key AND k.platform='spotify')
    """,
        prefix,
    )
    execute(
        """
        INSERT INTO backtest.readiness SELECT %s,%s,family,day,history_days,first_rank_day
        FROM {s}.mart_readiness
    """,
        prefix,
    )
    conn.execute(
        """
        INSERT INTO backtest.builds
        SELECT %s,%s,%s,%s,%s,count(*),count(*) FILTER(WHERE families>=2),
            (SELECT count(*) FROM backtest.predictions WHERE cycle_id=%s AND method=%s AND list='arrivals')
        FROM backtest.pool WHERE cycle_id=%s AND method=%s
    """,
        (*prefix, day, cycle["close_no"], method["sha"], *prefix, *prefix),
    )


def extract_billboard(conn, method, cycle):
    """Keep one outcome per movement group, with strict members for later label joins."""
    schema = method["schema"]
    if (
        conn.execute(
            "SELECT to_regclass(%s)", (schema + ".int_billboard_song__daily",)
        ).fetchone()["to_regclass"]
        is None
    ):
        return
    params = (cycle["id"], method["name"])
    for query in (
        """
        INSERT INTO backtest.billboard_members
        SELECT %s,%s,song_key,cluster_key FROM {s}.int_song_cluster__daily
        """,
        """
        INSERT INTO backtest.facts
        SELECT DISTINCT %s,%s,song_key,chart_week,'billboard_debut','hot-100',
            coalesce(is_debut,false),NULL::double precision
        FROM {s}.int_billboard_song__daily WHERE song_key IS NOT NULL
        """,
        """
        INSERT INTO backtest.targets VALUES (%s,%s,'billboard_debut','hot-100',NULL)
        """,
        """
        INSERT INTO backtest.observations
        SELECT %s,%s,'billboard_debut','hot-100',chart_week,bool_and(week_complete)
        FROM {s}.int_billboard_song__daily GROUP BY chart_week
        """,
        """
        INSERT INTO backtest.billboard_weeks
        SELECT %s,%s,chart_week,count(*),count(song_key)
        FROM {s}.int_billboard_song__daily GROUP BY chart_week
        """,
        """
        INSERT INTO backtest.billboard_blockers
        SELECT DISTINCT %s,%s,c.cluster_key,b.chart_week,'unkeyed_title'
        FROM {s}.int_billboard_song__daily b
        JOIN {s}.int_song_cluster_inputs__daily k ON {member_title}={entry_title}
        JOIN {s}.int_song_cluster__daily c ON c.song_key=k.song_key
        WHERE b.song_key IS NULL
        """,
        """
        INSERT INTO backtest.billboard_blockers
        SELECT DISTINCT %s,%s,song_key,chart_week,'unknown_debut'
        FROM {s}.int_billboard_song__daily
        WHERE song_key IS NOT NULL AND is_debut IS NULL
        """,
    ):
        conn.execute(
            sql.SQL(query).format(
                s=sql.Identifier(schema),
                member_title=sql.SQL(negative_title_sql("k.folded_title")),
                entry_title=sql.SQL(negative_title_sql("b.track_title")),
            ),
            params,
        )
