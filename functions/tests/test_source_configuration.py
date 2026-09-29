"""Tests for source configuration."""


import json
import os

import psycopg
from free_source_fixture import (
    ROOT,
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions.registry import discover


def test_every_free_sources_fixture_host_is_declared():
    for key in ("sz_chart", "wiki_sitelinks", "wiki_pageviews"):
        hosts = set(discover()[key].hosts)
        for path in (ROOT / key / "fixtures").glob("*.jsonl"):
            for line in path.read_text().splitlines():
                url = json.loads(line)["request"]["url"]
                assert url.split("/")[2] in hosts, (key, path.name, url)


def test_free_source_host_rates_apply_once_and_an_operator_rate_stands(databases):

    from conftest import url_database
    from mdp_functions.streamline_defaults import HOST_RATES, seed_streamline_defaults
    from psycopg.conninfo import conninfo_to_dict

    control = url_database(os.environ.get("MDP_CONTROL_RT_URL") or os.environ["MDP_CONTROL_RT_DATABASE_URL"],
                           conninfo_to_dict(databases["control_url"])["dbname"])
    assert HOST_RATES["www.shazam.com"] == 0.5
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.audit_log WHERE action='host_rate_default'")
        conn.execute("DELETE FROM control.host_health WHERE host=ANY(%s)", (list(HOST_RATES),))
    with psycopg.connect(databases["control_url"]) as functions, psycopg.connect(control) as rt_conn:
        seed_streamline_defaults(functions, rt_conn, {})
    with psycopg.connect(databases["admin_control"]) as conn:
        rates = dict(conn.execute("SELECT host,host_rps::float FROM control.host_health WHERE host=ANY(%s)",
                                  (list(HOST_RATES),)).fetchall())
        assert rates == HOST_RATES
        conn.execute("UPDATE control.host_health SET host_rps=0.25 WHERE host='www.shazam.com'")
        conn.execute("DELETE FROM control.audit_log WHERE action='host_rate_default'")
    # Even with its marker gone, a rate an operator set is not the default and stays.
    with psycopg.connect(databases["control_url"]) as functions, psycopg.connect(control) as rt_conn:
        seed_streamline_defaults(functions, rt_conn, {})
    with psycopg.connect(databases["admin_control"]) as conn:
        assert conn.execute("SELECT host_rps::float FROM control.host_health WHERE host='www.shazam.com'").fetchone() == (0.25,)


def test_a_seed_that_gained_a_column_is_recreated_by_the_deploy(databases, tmp_path):
    from mdp_functions.exporter import widened_seeds

    (tmp_path / "rights_registry.csv").write_text("source_key,license_ref,review_ref\nkexp_plays,unverified,\n")
    (tmp_path / "chart_caps.csv").write_text("source_key,cap,note\nsz_chart,60,x\n")
    (tmp_path / "fixture_rules.csv").write_text("rule,enabled\nfixture,false\n")
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS free_sources_reference")
        conn.execute("DROP TABLE IF EXISTS free_sources_reference.rights_registry, free_sources_reference.chart_caps")
        conn.execute("CREATE TABLE free_sources_reference.rights_registry (source_key text, license_ref text)")
        conn.execute("CREATE TABLE free_sources_reference.chart_caps (source_key text, cap integer, note text)")
        # A new seed has no table yet: a plain seed creates it.
        assert widened_seeds(conn, tmp_path, "free_sources_reference") == ["rights_registry"]
        conn.execute("DROP SCHEMA free_sources_reference CASCADE")
