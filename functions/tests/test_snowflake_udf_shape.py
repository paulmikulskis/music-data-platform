"""Offline Snowflake format check; never claims a live Snowflake invocation."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_receipt_columns_match_postgres():
    pg = (ROOT / "udf/postgres/mdp_invoke.sql").read_text()
    sf = (ROOT / "udf/snowflake/mdp_invoke.sql").read_text()

    def columns(sql):
        block = re.search(r"RETURNS TABLE\((.*?)\)\s*LANGUAGE", sql, re.DOTALL).group(1)
        block = re.sub(r"NUMBER\(38,0\)", "bigint", block, flags=re.IGNORECASE)
        return [
            (
                c.strip().split()[0],
                c.strip().split()[1].lower().replace("varchar", "text"),
            )
            for c in block.split(",")
        ]

    assert columns(sf) == columns(pg)
    code = sf.split("AS $$", 1)[1].split("$$;", 1)[0]
    ast.parse(code)
    for setting in (
        "EXTERNAL_ACCESS_INTEGRATIONS",
        "SECRETS",
        "get_generic_secret_string",
        "'/v1/invoke'",
        "'/v1/runs/'",
        "'Idempotency-Key'",
        "'dbt_run_id'",
        "'paused'",
        "allow_partial",
    ):
        assert setting in sf
    pg_payload = re.search(
        r"payload = \{k: p.get\(k\) for k in \((.*?)\)\}", pg, re.DOTALL
    ).group(1)
    sf_payload = re.search(
        r"payload = \{k: p.get\(k\) for k in \((.*?)\)\}", sf, re.DOTALL
    ).group(1)
    assert ast.literal_eval("(" + pg_payload + ")") == ast.literal_eval(
        "(" + sf_payload + ")"
    )
