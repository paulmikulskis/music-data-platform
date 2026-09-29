# Local database stacks

For the complete fixture stack, run `bash ops/local/up.sh` from the repository root.
Source [env.sh](env.sh) for matching service and dbt settings; `bash ops/local/down.sh`
stops the services and removes its container. See the [Quickstart](../../docs/DEVELOPING.md#quickstart).
The stacks below are separate, advanced database/image acceptance setups.

| Listener | Stack |
|---|---|
| 5433 | Existing mdp-test-pg control-page database, or standalone Compose mdp-pg |
| 5434 | Compose mdp-pg-lake with TLS |
| 9000 / 9001 | Compose MinIO S3 endpoint / console |
| 5435 | Separately provisioned SQL invocation/lifecycle fixture stack |

Select a stack deliberately and keep every role URL/service listener aligned. Do not start
another container on an occupied port. Local development settings stay in scripts/environment;
production uses separately provisioned secrets. See [setup](../../docs/DEVELOPING.md).

For an existing server, bootstrap with the intended administrator connection:

```sh
MDP_PG_HOST=127.0.0.1 MDP_PG_PORT=5433 bash ops/local/init.sh
```

For a fresh standalone server with 5433 free:

```sh
docker compose -f ops/local/docker-compose.yml up -d mdp-pg
MDP_PG_HOST=127.0.0.1 MDP_PG_PORT=5433 bash ops/local/init.sh
```

Wait for PostgreSQL readiness. init.sh creates databases/roles, applies migrations, registers
the warehouse and reconciles grants. Executing runs bootstrap; sourcing only exports settings.
POSTGRES_PASSWORD and MDP_PG_HOST/PORT/USER configure the local administrator.

For the image and object-store gates, start only their named services:

```sh
docker build -f ops/fly/postgres/Dockerfile -t mdp-postgres:local .
docker compose -f ops/local/docker-compose.yml up -d mdp-pg-lake mdp-minio
MDP_PG_PORT=5434 bash ops/local/init.sh
bash ops/local/run-gates.sh
```

Compose's one-shot dependency creates the bucket. WITH_PG_LAKE=0 explicitly builds a heap
fallback; plain up uses the existing image. The object store runs Chainguard's MinIO server and client
images (MinIO no longer publishes public images); compose pulls them.
[Image conventions](../fly/postgres/README.md) cover TLS, memory, grants and first boot.

Only intentionally disposable data may be reset. Stop/remove the selected services and
inspect their exact named volumes before deletion; never use compose down -v on shared stacks.
The lost+found gate needs its fixture before a fresh image database first boots:

```sh
docker compose -f ops/local/docker-compose.yml run --rm --no-deps --entrypoint sh mdp-pg-lake -c 'test ! -e /var/lib/postgresql/data/pgdata/PG_VERSION && mkdir -p /var/lib/postgresql/data/lost+found'
```

The gate runner fails required errors and labels extension absence HEAP-FALLBACK. Its grant
adapter proves wb_* creation and rejects other workbench schemas in a disposable proof database.
Gate evidence records actual observations and storage limits.
