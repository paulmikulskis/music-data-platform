"""Tests for input identity encoding."""


from pathlib import Path

import psycopg


def test_identity_serialization_is_unambiguous(databases):
    macro = Path("dbt/macros/mdp_input_identity.sql").read_text()
    assert "jsonb_build_array" in macro
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        null, literal, ab_c, a_bc = conn.execute(
            "SELECT md5(jsonb_build_array(NULL::text)::text),md5(jsonb_build_array('<null>'::text)::text),md5(jsonb_build_array('a'||chr(31)||'b','c')::text),md5(jsonb_build_array('a','b'||chr(31)||'c')::text)"
        ).fetchone()
        assert null != literal and ab_c != a_bc


def test_track_identity_null_components_are_invalid(databases):
    import re

    source = Path("dbt/models/intermediate/int_chart_slot_identity.sql").read_text()
    expression = re.search(
        r"as entity_id,\s*(.*?) as valid_components", source, re.DOTALL
    ).group(1)
    assert "IS NOT TRUE" in Path("dbt/tests/track_identity_components.sql").read_text()
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        for values in [
            ("chart", "territory", "week", "track"),
            (None, "territory", "week", "track"),
            ("chart", None, "week", "track"),
            ("chart", "territory", None, "track"),
            ("chart", "territory", "week", None),
            ("chart\x1fother", "territory", "week", "track"),
        ]:
            result = conn.execute(
                "SELECT "
                + expression
                + " FROM (VALUES (%s::text,%s::text,%s::text,%s::text)) AS candidates(chart,territory,week,canonical_track_id)",
                values,
            ).fetchone()[0]
            assert result is (values == ("chart", "territory", "week", "track"))
