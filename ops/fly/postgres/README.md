# Postgres image

Build from the repository root so the context includes the database/role SQL and UDF:

```sh
docker build --build-arg WITH_PG_LAKE=1 -f ops/fly/postgres/Dockerfile -t mdp-postgres:local .
docker buildx build --platform linux/amd64 --load --build-arg WITH_PG_LAKE=1 -f ops/fly/postgres/Dockerfile -t mdp-postgres:amd64 .
```

The Debian bookworm Postgres 17 runtime includes plpython3u, httpx, TLS and supervisord.
A separate builder compiles pg_lake 3.5.1 at commit 2dfa035011b041ffcf2e31ba82d5c20ded3c9196,
including pgduck_server and DuckDB extensions. Sources/compilers stay outside the runtime.
WITH_PG_LAKE=1 is the default; =0 explicitly builds a heap-only image. Python extension
packages match the base image's PG_VERSION. CI pulls the base before building; update an
unavailable PGDG patch deliberately or use its matching archive.

Fly evidence records a successful native amd64
build and installed pg_lake SQL extensions at version 3.5. The retained volume preserves
chart rows across replacement. Local gates record
arm64 pg_lake and amd64 heap checks. Do not infer an image's features from its tag alone.
Heap is the active default. Iceberg enablement requires object storage and restore acceptance;
the recorded restore of a writable table at a retained non-empty location fails that gate.


## Boot and access

Boot renders private configuration under /run/mdp and generates self-signed TLS when no
certificate/key pair is mounted. Supervisord restarts PostgreSQL and the enabled sidecar.
pgduck_server runs as postgres with an explicit memory limit and shared PGDATA/base/pgsql_tmp;
its socket is local. Health checks query PostgreSQL and the sidecar separately.
PGDATA=/var/lib/postgresql/data/pgdata leaves the volume root available for lost+found.

On empty PGDATA, init scripts run as superuser: database/role SQL, extensions, warehouse
grants, private mdp configuration and UDF installation. Drizzle separately migrates control.
Existing volumes need explicit migration and functions/udf/postgres/install.sh; first-boot
changes alone do not update them. Warehouse bootstrap is transactional and rerunnable.

Defaults and scoped event triggers grant readers marts/tenant schemas and the ten raw
lineage columns, not raw payloads. Workbench DDL is confined to wb_* schemas and has no mdp
access. Its CREATE SCHEMA must be a single plain statement; prefix-violating rename/ownership
changes fail. PUBLIC function execution is revoked; only dbt_transform invokes the UDF.
loader_wh receives pg_lake capability and warehouse TEMPORARY permission when installed.

Fly PostgreSQL has private networking; the HAProxy frontend passes TCP through. PostgreSQL
terminates TLS and rejects plaintext for private and forwarded IPv4/IPv6 connections.
sslmode=require proves encryption; verify-full also requires a trusted issuer/root certificate.
Mounted TLS replacement uses server.crt/server.key under /var/lib/postgresql/tls, owned by
postgres with private key mode 600; reload/restart applies a mounted replacement.


## Configuration

| Input | Purpose |
|---|---|
| POSTGRES_PASSWORD | First-boot administrator secret |
| MDP_ROLE_PASSWORD_<ROLE> | Nine role secrets, uppercase role suffix |
| MDP_PG_HOSTNAME | Certificate hostname |
| PGDUCK_MEMORY_LIMIT | Sidecar memory bound, default 1GB |
| R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY | Production object store |
| MDP_S3_ENDPOINT, MDP_S3_ACCESS_KEY, MDP_S3_SECRET_KEY | Local S3-compatible stand-in |

Object-store secrets live in a mode-600 sidecar init file, never GUCs, build arguments or
image layers. Rotating database secrets also requires administrator ALTER ROLE operations.
Keep database backups and retained object files together; the sidecar cache is disposable.
`storage="iceberg"` selects CREATE TABLE USING iceberg only when availability gates pass;
a heap-only image cannot silently satisfy an Iceberg declaration.


## Staff query review

The image adds `pg_stat_statements` to `shared_preload_libraries`, beside `pg_extension_base`
when pg_lake is enabled. **Production needs one Postgres restart between cycles.** Stop the
runner between cycles, replace the image, restart Postgres once, and apply bootstrap before
resuming the runner. Rebuild the global and every tenant’s shared models before reopening
access; stored marts keep old values until rebuilt. Bootstrap installs the extension and clears old role and database logging
settings. Existing-volume init scripts do not run again. This is a warehouse change, with no
control migration. The explore group ships empty.

The supervised worker reads normalized counters by login. `track_utility=off` excludes password
DDL. A login cannot switch to the shared explore role, so its queries retain the individual role.
Only the hash, observed time, call-count delta, tenants and labels enter `catalog.query_audit`.
The worker strips comments and any remaining literals before parsing. It never executes the query.
Workbenches keep their own result labels and audit path.

Counters are aggregated, not a complete execution log. Eviction, a restart, a removed role or a
failed execution can leave a gap. The observed time is not the execution time. Unknown scope
stays red, as do mixed tenant inputs. Filters can narrow the actual result without removing the
input warning. `/queries` puts mixed inputs first.

SQL statement, duration and error-statement logging are disabled. Postgres stdout and stderr
have no log sink, including during boot. No SQL log file exists on the database volume.
Boot removes the old `query-log` directory. Statistics are capped at 1,000 entries, are not saved
at shutdown, and keep representative text in `/dev/shm` through `pg_stat_tmp`, away from the
volume. Shared memory has a fixed container size; it cannot grow the volume if the worker stops.
Representative text is administrator-only and may retain comments until normalized by the worker.
The audit uses a 100,000-slot ring and expires rows after 30 days. The slot limit is enforced by
Postgres on every insert, independently of the worker. Worker messages contain no SQL or values
and stream to the container runtime; the supervisor writes no local log file.

The [Postgres 17 statistics documentation](https://www.postgresql.org/docs/17/pgstatstatements.html)
explains preload, normalization and counter limits. See [analyst access](../../../docs/analyst-access.md#query-everything)
for login commands and evidence for local proofs.

Staff reads are declared per column in the generated label catalog. Schema-wide SELECT
grants do not exist for analyst, explorer or workbench roles. Unknown columns are null in
`explore_<schema>` views. Personal keys use their declared pseudonyms; free-form payloads
are null. Retained comment inputs are dbt-only, and the disabled enrichment worker refuses
them. Bootstrap reconciles old table and column grants; rebuild shared models before access resumes.
