"""Tests for source defaults."""


import os

from mdp_functions.registry import discover


def test_streamline_defaults_apply_once_on_new_rows_only(databases):
    """Declared knobs land on a new streamline whether or not registry sync ran
    first; gated collectors start disabled; the paired Spotify collector inherits the
    retired embed knobs with double the timeout; retired rows (the Spotify pair) stay and stop; a later seed never overrides an operator's choice."""

    import psycopg
    from conftest import url_database
    from mdp_functions.streamline_defaults import (
        RETIRED,
        SIGNATURES,
        seed_streamline_defaults,
    )
    from psycopg.conninfo import conninfo_to_dict

    catalog = discover()
    gated = sorted(k for k, m in catalog.items() if m.knobs.get("enabled") is False)
    assert {"lb_sitewide", "sc_playlist", "kexp_plays", "mb_spine"} <= set(gated)
    assert all(not catalog[key].knobs["enabled"] for key in gated)
    assert not set(RETIRED) & set(catalog)
    declared = [k for k, m in catalog.items() if m.knobs]
    touched = [*declared, *RETIRED]
    functions_url = databases["control_url"]
    control_url = url_database(
        os.environ.get("MDP_CONTROL_RT_URL")
        or os.environ["MDP_CONTROL_RT_DATABASE_URL"],
        conninfo_to_dict(functions_url)["dbname"],
    )
    with psycopg.connect(control_url) as conn:
        saved = conn.execute(
            "SELECT source_key,enabled,batch_size,max_concurrency,timeout_s FROM control.streamline WHERE source_key=ANY(%s)",
            (touched,),
        ).fetchall()
    try:
        # Setup runs as the database owner: the roles cannot delete streamlines.
        with psycopg.connect(databases["admin_control"]) as conn:
            for key in (*RETIRED, "am_playlist", "sc_playlist"):
                conn.execute(
                    "INSERT INTO control.streamline(source_key,layer) VALUES (%s,'bronze') "
                    "ON CONFLICT(source_key) DO NOTHING",
                    (key,),
                )
            # sc_playlist and am_playlist exist already (registry sync ran first);
            # every other declared streamline without runs is created by the seed.
            conn.execute(
                "DELETE FROM control.streamline WHERE source_key=ANY(%s) AND source_key NOT IN "
                "('sc_playlist','am_playlist') AND NOT EXISTS "
                "(SELECT 1 FROM control.run r WHERE r.streamline_id=control.streamline.id)",
                (declared,),
            )
            conn.execute(
                "UPDATE control.streamline SET enabled=true,batch_size=1,max_concurrency=1,timeout_s=300,allow_partial=false WHERE source_key=ANY(%s)",
                (touched,),
            )
            conn.execute(
                "UPDATE control.streamline SET batch_size=5,max_concurrency=2,timeout_s=1500 WHERE source_key='sp_playlist_embed'"
            )
            # An operator already tuned am_playlist: the seed leaves it alone.
            conn.execute(
                "UPDATE control.streamline SET timeout_s=2400 WHERE source_key='am_playlist'"
            )
            conn.execute(
                "DELETE FROM control.audit_log WHERE action IN ('streamline_defaults','streamline_retired','identifier_override')"
            )
        with (
            psycopg.connect(functions_url) as fconn,
            psycopg.connect(control_url) as cconn,
        ):
            applied = seed_streamline_defaults(fconn, cconn, catalog)
        assert set(applied) == set(declared) - {"am_playlist"}
        with psycopg.connect(control_url) as conn:
            knobs = {
                r[0]: r[1:]
                for r in conn.execute(
                    "SELECT source_key,enabled,batch_size,max_concurrency,timeout_s FROM control.streamline WHERE source_key=ANY(%s)",
                    (touched,),
                ).fetchall()
            }
            assert all(knobs[k][0] is False for k in gated)
            assert knobs["sp_playlist"] == (True, 5, 2, 3000)
            assert knobs["sp_playlist_weekly"] == (True, 5, 2, 3000)
            assert knobs["bc_discover"] == (False, 5, 1, 1500)
            assert knobs["am_playlist"] == (True, 1, 1, 2400)
            assert all(knobs[k][0] is False for k in RETIRED)
            retired = conn.execute(
                "SELECT subject FROM control.audit_log WHERE action='streamline_retired' ORDER BY subject"
            ).fetchall()
            assert [r[0] for r in retired] == sorted(RETIRED)
            # Each applied streamline has its declared timeout, never the table default in its place.
            assert all(
                v[3] == catalog[k].knobs["timeout_s"]
                for k, v in knobs.items()
                if k in applied and "timeout_s" in catalog[k].knobs
            )
            assert not any(
                v[3] == 300
                for k, v in knobs.items()
                if k in applied and catalog[k].knobs.get("timeout_s", 300) != 300
            )
            hosts = dict(
                conn.execute(
                    "SELECT host,last_signature FROM control.host_health WHERE host=ANY(%s)",
                    (list(SIGNATURES),),
                ).fetchall()
            )
            assert hosts == SIGNATURES
            reason = conn.execute(
                "SELECT \"after\"->>'reason' FROM control.audit_log WHERE action='identifier_override'"
            ).fetchall()
            assert reason == []
            # An operator enables a gated collector; a later seed keeps it enabled.
            conn.execute(
                "UPDATE control.streamline SET enabled=true WHERE source_key='sc_playlist'"
            )
        with (
            psycopg.connect(functions_url) as fconn,
            psycopg.connect(control_url) as cconn,
        ):
            assert seed_streamline_defaults(fconn, cconn, catalog) == []
        with psycopg.connect(control_url) as conn:
            assert conn.execute(
                "SELECT enabled FROM control.streamline WHERE source_key='sc_playlist'"
            ).fetchone() == (True,)
    finally:
        # The session database is shared: restore the knobs and markers found.
        with psycopg.connect(databases["admin_control"]) as conn:
            for key, *values in saved:
                conn.execute(
                    "UPDATE control.streamline SET enabled=%s,batch_size=%s,max_concurrency=%s,timeout_s=%s WHERE source_key=%s",
                    (*values, key),
                )
            conn.execute(
                "UPDATE control.streamline SET enabled=true WHERE source_key=ANY(%s) AND source_key<>ALL(%s)",
                (touched, [row[0] for row in saved]),
            )
            conn.execute(
                "DELETE FROM control.audit_log WHERE action IN ('streamline_defaults','streamline_retired','identifier_override')"
            )
