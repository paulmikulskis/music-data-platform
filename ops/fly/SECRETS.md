# Secret-store adapter contract

This public snapshot does not include a secret-manager client or deployment credentials.
Commands named `secret_store` below are an operator-supplied adapter on PATH. Before
using deploy or preflight, implement that interface for your own secret manager:

- `run -- COMMAND ...` injects the selected configuration into a child process.
- `export json|env` writes the selected configuration to stdout. JSON preserves exact string values; env uses one `KEY="value"` assignment per line, escaping backslashes, double quotes and newlines as `\\`, `\"` and `\n`. The reader never evaluates shell expressions.
- `get KEY` writes one exact value to stdout.
- `set KEY` reads the exact value from stdin, writes it to the selected configuration and emits no value.

`SECRET_STORE_PROJECT` and `SECRET_STORE_CONFIG` select an operator-defined namespace and configuration through the environment. Export them before running these commands. Authentication is implementation-specific; `SECRET_STORE_TOKEN` is optional if the adapter uses it. Commands return nonzero on failure and never prompt interactively.
The adapter must keep secret values out of diagnostic logs. Placeholder domains and project
names in this guide are examples. Local offline tests do not need this adapter.


The selected secret-manager configuration is the source of truth.
`python3 ops/fly/secret-map.py provision` generates absent role passwords, the
service token and the promoters' control-API key (`MDP_CONTROL_API_KEY`) with
`openssl rand -hex 32`, sends each on stdin to `secret_store set KEY`, and derives private TLS DSNs and
`MDP_CONTROL_API_URL`. It never replaces existing passwords or prints values.
`ops/fly/bootstrap.py` stores that key's sha256 in `control.api_key` as role `promoter`
(label `mdp-functions promoters`), which reaches only the target commands
`ControlTargets` calls; the data API refuses it. To rotate it, delete the secret manager key
and deploy: provision mints a new one, and bootstrap revokes the old row. Existing database password
rotation also requires `ALTER ROLE` through the administrator; an environment
update alone does not rotate a database role.

The executable allowlist in `secret-map.py` expands the wildcard groups and filters
`secret_store export json` before piping directly into `fly secrets import`. No temporary secret
files are created. Migrator and administrator DSNs are used only by provisioning.

`fly secrets import` does not unescape, so the filter reads JSON (exact values, not
an escaped env format) and writes each value in the one form flyctl reads back
unchanged: `KEY="value"`, or `KEY="""value"""` when the value holds a double quote
(JSON). A value with a line break, a double quote together with `#`, three double
quotes in a row, a leading or trailing single quote without a double quote, or more
than 64 KiB refuses the whole import with `FAIL secret <KEY> ...` and a next step;
nothing reaches Fly. A key absent from the secret manager writes nothing, so Fly keeps its value.

Commands below are placeholders; replace bracketed text through a secure stdin
source instead of entering credentials in shell history:

Once Alloy exists, the service's app-specific OTLP endpoint should be
`http://mdp-alloy.internal:4318`; Alloy retains the upstream HTTPS endpoint and auth
header. `OTLP_HEADERS` is the runtime's exporter header setting; `OTLP_AUTH_HEADER`
is the single upstream Authorization header consumed by Alloy.

Explicit DSN overrides (normally derived by provisioning) can be written with
`secret_store set MDP_CONTROL_URL` and the value on stdin. The same interface handles
other DSNs, service URLs and `MDP_DUMP_ROOT`. Never pass credentials in command arguments.

The functions app also imports `MDP_CONTROL_RT_URL`, `MDP_WORKBENCH_WH_URL`,
`MDP_WORKBENCH_ADMIN_URL`, and `MDP_READER_URL` for the workbench process.
Its administrator connection provisions and expires isolated per-session roles;
ordinary previews use those restricted roles. `MDP_WORKBENCH_URL` points the
control API at `workbench.process.mdp-functions.internal:8085`. These values are
derived and stored in the secret manager, then imported through the same filtered pipeline.

`MDP_MB_DB_URL` comes from the mirror import. Store it in the same project and config before deploying identity functions. Without it, mb_spine and mb_resolve fail `vendor_4xx` and the reference probe records the error.


## Admin keys

CLI administration uses a global admin API key independently of Clerk. Install the locked
control dependencies with `pnpm --dir control install --frozen-lockfile`. Supply
`MDP_CONTROL_DATABASE_URL` securely for the `control` database as `migrator` or an administrator.
The URL must reach Postgres through its own connection, such as the
[analyst proxy](../../docs/analyst-access.md#fly-account-and-connection) with privileged credentials.

The owner issues each label once. Capture the raw key without printing it:

```sh
MDP_API_KEY="$(ops/fly/postgres/admin-add.sh operator-console)" && export MDP_API_KEY
```

Only the SHA-256 hash enters `control.api_key`; stdout contains the raw key once. Keep it in a
secret manager and load it from there in subsequent sessions. This is an operator credential,
not an app secret in the deployment allowlist. Reader keys from `keys create` cannot administer control.

To store the raw key directly in the secret manager without printing it, use
`ops/fly/postgres/admin-add.sh operator-console --secret_store <project>/<config>/MDP_API_KEY`.
Then, with the [control proxy and URL](../../docs/operating.md#reach-control-api) configured, run
`secret_store run -- pnpm --dir control mdp status`.
The secret manager receives the key on stdin. A failed write rolls back the insert; check the destination
before retrying because the secret manager and Postgres cannot commit atomically.

Revoke with `ops/fly/postgres/admin-revoke.sh operator-console` using the same privileged URL.
Revocation applies to the next request, preserves the row, and is safe to repeat. An unrevoked
admin label cannot be issued twice; revoke it before reusing the label. Clerk remains the browser
sign-in path; API keys work without Clerk.


## Showcase

`mdp-showcase` requires `MDP_SHOWCASE_PEOPLE`, `MDP_SHOWCASE_LINK_SECRET`,
`MDP_SHOWCASE_SESSION_SECRET`, `MDP_SHOWCASE_WH_URL`, `MDP_CONTROL_RT_URL`,
`MDP_SHOWCASE_READER_KEY`, `MDP_CONTROL_API_URL`, `MDP_DATA_API_URL`, `MDP_SERVICE_URL` and `MDP_SERVICE_TOKEN`.
Mint `MDP_SHOWCASE_READER_KEY` with `pnpm --dir control mdp keys create --role reader --global --label showcase-reader`;
step 4 of [Give a viewer access](../../docs/operating.md#give-a-viewer-access) stores it in the secret manager without printing it.
A reader key reads global marts through the data API and nothing else: control-api refuses it, and so does every tenant mart.
The service credentials deliver storage snapshot alerts. The job runs at 00:30 UTC.
People and keys are runtime values; do not put them in the repository.
Store `MDP_SHOWCASE_PEOPLE` as compact one-line JSON, with any `#` in a name written as `\u0023`; follow [Give a viewer access](../../docs/operating.md#give-a-viewer-access).
Provisioning generates the two signing secrets as 32 random bytes and derives the warehouse URL
from `MDP_ROLE_PASSWORD_SHOWCASE_WH`, using `mdp-postgres.internal:5432` with `sslmode=require`.
The Postgres app receives the role password. The showcase receives only its derived URL.
The role SQL applies to fresh and existing volumes, with inherited explorer access and four connections.

`mdp-control-api` requires `MDP_TRUSTED_BROWSER_ORIGINS`.
The provisioned value is `https://mdp-showcase.example.invalid`. Multiple origins use commas.
Each must be an exact HTTP or HTTPS origin without a path or trailing slash.

`MDP_SHOWCASE_IDLE_DAYS` and `MDP_SHOWCASE_MAX_DAYS` are optional on `mdp-showcase`.
They default to 14 and 60 days. Both accept whole numbers from 1 through 365;
idle days cannot exceed maximum days. Invalid values stop startup. Set them and restart the app.
`MDP_SHOWCASE_ORIGIN` is optional on showcase and control-api; both default to
`https://mdp-showcase.example.invalid`. Set the same origin on both when using another address.

Control-api receives `MDP_SHOWCASE_PEOPLE` and `MDP_SHOWCASE_LINK_SECRET` to mint links.
They are optional there until viewer access is configured, and remain required on showcase.
Without them, link minting refuses with setup guidance. After setting or rotating either,
deploy control-api and showcase together using [Give a viewer access](../../docs/operating.md#give-a-viewer-access).

Deploy-time inputs stay on the operator machine. They are not forwarded by `secret-map.py filter`.
`secret-map.py check mdp-showcase` also requires a non-empty `MDP_SHOWCASE_DENY_NAMES`
in the selected secret-manager configuration. Store the private deny list as one name per line; never print it.
The check lists the other deploy prerequisites but does not test Git history or API access:
full Git history, plus `MDP_SHOWCASE_TENANT_COUNT` from a current read-only count or a working
`mdp tenants list` connection. The deploy enables `MDP_SHOWCASE_PROBE_HOSTS=1` itself.
Follow [Showcase deploy inputs](../../docs/operating.md#showcase-deploy-inputs), then rerun
`python3 ops/fly/secret-map.py check mdp-showcase`.

The house reader and model integration are optional at first.
When enabling them, run `python3 ops/fly/secret-map.py check mdp-showcase --integrations` and
`python3 ops/fly/secret-map.py check mdp-functions --integrations`.
Enabling these integrations requires `MDP_SHOWCASE_HOUSE_READER_KEY` on showcase and
`MDP_LITELLM_BASE_URL`, `MDP_LITELLM_KEYS`, `MDP_LITELLM_ADMIN_KEY` on functions.
Run the default check before the first deployment; it does not require these later inputs.


## Alert email

Set `RESEND_API_KEY` or `SMTP_URL` for `mdp-control-api`.
Set `MDP_EMAIL_FROM` to the sender and `MDP_EMAIL_TO` to the recipient.
These four names are optional, so a missing transport does not stop deployment.
`python ops/fly/secret-map.py check mdp-control-api` names missing settings without printing values.
The email worker sends critical alerts and selected warning classes that remain open and unacknowledged.
Check delivery in the `email.sent` audit row at `/audit`.


## External heartbeat

Set optional `MDP_HEARTBEAT_URL` for `mdp-core-runner` to a monitor's secret ping URL.
The runner sends one GET after a successful scheduled build with a closed, mirrored cycle.
Manual work, Replay and restore do not send it.
Without the secret, the runner logs one skipped message.
Configure the monitor's deadline and destination outside this database.
A shared URL tracks runner activity; choose a deadline that allows the hourly schedule and its retry.
It does not prove every cadence is fresh; check `/ops` for each cadence.
Follow the drill in [service unreachable](../runbooks/service_unreachable.md#external-heartbeat).
