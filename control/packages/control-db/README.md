# @mdp/control-db

Drizzle owns the control schema: configuration and work state, never warehouse output.
`src/schema/` defines 38 tables, enums, constraints, indexes and the landed_seq sequence.
`src/index.ts` exports the schema, `createControlDb(url)` and table select schemas.
Close a factory connection with `db.$client.end()` at shutdown.

Edit schema → `pnpm generate` → review SQL → `pnpm migrate` from this package.
MDP_CONTROL_DATABASE_URL supplies the migrator connection. Cluster roles are provisioned
separately by [sql/databases.sql and sql/roles.sql](sql/README.md).

`pnpm prove-grants` creates a disposable control database, checks table/column permissions
with the ten role logins, and removes it. Supply MDP_CONTROL_ADMIN_URL plus
MDP_<ROLE>_DATABASE_URL for each uppercase role name. Warehouse-only roles prove denied
control access and use warehouse connections for their checks. On a shared cluster,
MDP_GRANTS_PROOF_PREFIX names the disposable database and MDP_GRANTS_WAREHOUSE_DATABASE the
warehouse database. Evidence goes to ops/evidence/platform/grants-*.txt.
[Local init](../../../ops/local/README.md) performs bootstrap, migrations and warehouse
registration when executed; sourcing it only exports configuration.
