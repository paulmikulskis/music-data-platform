# Bootstrap order

Run `databases.sql`, then `roles.sql` against the maintenance database as the cluster administrator, then `drizzle-kit migrate` as migrator. Databases are created before roles, then `roles.sql` transfers ownership of `control` to migrator. Both SQL files are idempotent; roles.sql updates passwords from supplied variables on reruns. No password is embedded in either SQL file.

From the repository root, with PGHOST, PGPORT, PGUSER and PGPASSWORD supplied externally:

```sh
psql -X -v ON_ERROR_STOP=1 -d postgres -f control/packages/control-db/sql/databases.sql
psql -X -v ON_ERROR_STOP=1 -d postgres \
  -v migrator_password="$MIGRATOR_PASSWORD" \
  -v rights_sync_password="$RIGHTS_SYNC_PASSWORD" \
  -v control_rt_password="$CONTROL_RT_PASSWORD" \
  -v functions_rt_password="$FUNCTIONS_RT_PASSWORD" \
  -v service_read_password="$SERVICE_READ_PASSWORD" \
  -v loader_wh_password="$LOADER_WH_PASSWORD" \
  -v dbt_transform_password="$DBT_TRANSFORM_PASSWORD" \
  -v workbench_wh_password="$WORKBENCH_WH_PASSWORD" \
  -v reader_wh_password="$READER_WH_PASSWORD" \
  -v api_key_reader_password="$API_KEY_READER_PASSWORD" \
  -v showcase_wh_password="$SHOWCASE_WH_PASSWORD" \
  -f control/packages/control-db/sql/roles.sql
cd control
pnpm --filter @mdp/control-db migrate
```

Set `MDP_CONTROL_DATABASE_URL` to the migrator connection string before migration. The first migration creates the schema as migrator; the second grants control access. Cluster bootstrap revokes PUBLIC database access. Only migrator, rights_sync, control_rt, functions_rt and api_key_reader can connect to control; api_key_reader reads only api_key and tenant, for data-API key checks. The five warehouse credentials receive only CONNECT on warehouse here; warehouse grants are installed by ops/fly/postgres/boot/init/10-warehouse-grants.sql and the image bootstrap. `NOINHERIT` is set for all ten roles.

For the proof, provide `MDP_CONTROL_ADMIN_URL` and `MDP_MIGRATOR_DATABASE_URL`, `MDP_RIGHTS_SYNC_DATABASE_URL`, `MDP_CONTROL_RT_DATABASE_URL`, `MDP_FUNCTIONS_RT_DATABASE_URL`, `MDP_SERVICE_READ_DATABASE_URL`, `MDP_LOADER_WH_DATABASE_URL`, `MDP_DBT_TRANSFORM_DATABASE_URL`, `MDP_WORKBENCH_WH_DATABASE_URL`, `MDP_READER_WH_DATABASE_URL`, `MDP_API_KEY_READER_DATABASE_URL`. Database paths are selected by the harness. Each role's statements run with its own login and one connection at a time, inside BEGIN/ROLLBACK; denied connections are checked before a transaction is possible. The harness creates a disposable control_proof_<epoch> database, migrates it as migrator, and drops it in finally (including SIGINT/SIGTERM). Fixtures and sequence increments exist only there; it never connects to control. No process can guarantee cleanup after SIGKILL or host failure; an administrator must remove an orphaned control_proof_* database in that case. A complete table/column matrix generates positive and negative ACL probes; real INSERTs also exercise each writer. Warehouse connections check CONNECT and forbidden DDL on the configured warehouse. Output never includes connection URLs.


# Schema boundaries

- Cursors use UNIQUE NULLS NOT DISTINCT over their natural identity because target_id is nullable.
- The production warehouse has a partial unique index plus a deferred exactly-one constraint;
  change the pin in one transaction. Local bootstrap seeds the initial row; TRUNCATE is refused.
- Immutable cycles, target exports/membership, events and prompts retain provenance. Counts and
  cursor versions use bigint; structured cursors/checkpoints use jsonb.
- batch.dump_ids records every output alongside cursor advancement; dump_id is the primary output.
  Resolved targets require platform_account_id. Cycle attempts record runner/job and build identity.
- control_rt owns configuration, workbench sessions and alert acknowledgement/resolution;
  functions_rt owns permitted runtime facts and code-owned registry fields. Prompt publication is
  insert-only through the PR workflow. Warehouse registration is migrator-owned.
- Authentication records store hashed credentials, expiry, revocation, roles and tenant scope.
- TypeScript remains strict; skipLibCheck isolates upstream declarations. SQL bigint defaults keep
  Drizzle snapshots serializable. Schema definitions and migrations are the executable authority.
