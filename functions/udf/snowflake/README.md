# Snowflake invocation format

`mdp_invoke.sql` is a Python table UDF with the same nine receipt columns,
request fields, transport idempotency key, deadlines, failure and partial policy
as the Postgres UDF. Bigint receipt strings become NUMBER(38,0) at the SQL boundary.

Before installation, create `mdp_service_rule` as an EGRESS HOST_PORT network rule
restricted to the public HTTPS functions hostname. Create two GENERIC_STRING secrets,
`mdp_service_token` and `mdp_service_url`, using secret deployment inputs. Grant the
installing role READ on those secrets and USAGE on the external access integration;
grant callers only USAGE on the function. No token is a SQL invocation argument.
Provision the control warehouse row and validated cycle binding before calling:

```sql
SELECT * FROM TABLE(mdp_invoke('billboard_hot100',
  OBJECT_CONSTRUCT('dbt_run_id', '<validated-run-id>', 'cadence', 'weekly',
                   'model', 'manual_format_check', 'deadline_s', 120)));
```

Verify offline: `uv run --project functions pytest functions/tests/test_snowflake_udf_shape.py`.
Live trial: **BLOCKED-INPUT** — Snowflake trial credentials, a public service hostname,
and its network rule/secrets are absent. The offline check proves format parity,
not Snowflake execution or Snowflake landing.

Integration setup follows [Snowflake external network access](https://docs.snowflake.com/en/developer-guide/external-network-access/creating-using-external-network-access).
