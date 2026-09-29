import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { selectSchemas } from "../src/index.js";

const controlRoles = [
  "migrator",
  "rights_sync",
  "control_rt",
  "functions_rt",
  "api_key_reader",
];
const warehouseRoles = [
  "service_read",
  "loader_wh",
  "dbt_transform",
  "workbench_wh",
  "reader_wh",
];
const registry = [
  "source_key",
  "layer",
  "tenant_bound",
  "writes",
  "reads",
  "external",
  "llm_step_id",
  "cadence_tag",
];
const knobs = [
  "enabled",
  "allow_partial",
  "batch_size",
  "max_concurrency",
  "timeout_s",
  "storage",
  "acknowledged_fingerprints",
];
const transport = ["transport_override", "proxy_provider", "proxy_country"];
// host_health splits by column: the service records blocks, the control plane sets policy.
const hostObserved = ["host", "blocked_until", "last_signature", "updated_at"];
const hostPolicy = [
  "host",
  "host_rps",
  "http_version",
  "user_agent",
  "robots_policy",
  "robots_override_by",
  "updated_at",
];
const alertFacts = [
  "id",
  "class",
  "severity",
  "subject_type",
  "subject_id",
  "run_id",
  "opened_at",
  "runbook_slug",
  "created_at",
  "updated_at",
];
const acknowledgement = [
  "acknowledged_by",
  "resolved_at",
  "resolved_by",
  "resolution_reason",
];
// reference_source: the functions probe writes mirror and landing state; control_rt only requests a re-import.
const reimportRequest = [
  "reimport_requested_at",
  "reimport_requested_by",
  "updated_at",
];
type Group =
  | "config"
  | "ledger"
  | "warehouse"
  | "prompt"
  | "rights"
  | "streamline"
  | "alert"
  | "cursor"
  | "api_key"
  | "host"
  | "reference"
  | "showcase"
  | "showcase_actor"
  | "showcase_call";
type Policy = { group: Group; insert: string };
// Independent specification of every table's writer and a valid, real INSERT.
// Shared referenced fixtures live only in the disposable database.
const ref = (table: string) => `(SELECT id FROM control.${table} LIMIT 1)`;
const policies = {
  showcase_link: {
    group: "showcase",
    insert:
      "(nonce, handle, expires_at, created_by) VALUES ('proof', 'proof', now(), 'proof')",
  },
  showcase_session: {
    group: "showcase",
    insert:
      "(id_hash, handle, csrf_token, expires_at) VALUES ('proof', 'proof', 'proof', now())",
  },
  showcase_actor: {
    group: "showcase_actor",
    insert:
      "(api_key_id, handle, display_name) VALUES ('00000000-0000-4000-8000-000000000001', 'proof', 'Test viewer')",
  },
  showcase_share: {
    group: "showcase",
    insert:
      "(slug, handle, query_id, class, params, payload, expires_at) VALUES ('proof', 'proof', 'proof', 'operational', '{}', '{}', now())",
  },
  showcase_seen: {
    group: "showcase",
    insert:
      "(handle, scope, close_no, seen_at) VALUES ('proof', 'global', 1, now())",
  },
  showcase_inventory: {
    group: "showcase",
    insert:
      "(day, warehouse, layer, relations, rows_est, bytes, captured_at, complete) VALUES (current_date, 'proof', 'raw', 0, 0, 0, now(), true)",
  },
  showcase_call: {
    group: "showcase_call",
    insert:
      "(author, author_kind, idempotency_key, song_key, anchors, snapshot, facts, facts_day, close_no, week_start) VALUES ('proof', 'ear', 'proof', 'apple:proof', '{}', 'proof', '{}', current_date, 1, current_date)",
  },
  warehouse: {
    group: "warehouse",
    insert:
      "(adapter, database, dsn_secret_ref) VALUES ('postgres', 'proof', 'MDP_WAREHOUSE_URL')",
  },
  runner_mode: { group: "config", insert: "DEFAULT VALUES" },
  tenant: { group: "config", insert: "(slug, name) VALUES ('new', 'proof')" },
  api_key: {
    group: "api_key",
    insert:
      "(key_hash, label, role) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'proof', 'reader')",
  },
  budget: {
    group: "config",
    insert:
      "(scope, period, cap_cents, soft_pct, hard_action) VALUES ('global', 'proof', 0, 0, 'warn')",
  },
  prompt: {
    group: "prompt",
    insert: "(name, version, body) VALUES ('proof', 1, 'proof')",
  },
  llm_step: {
    group: "config",
    insert:
      "(source_key, model, params_hash, step_version) VALUES ('proof', 'proof', 'proof', 1)",
  },
  streamline: {
    group: "streamline",
    insert: "(source_key, layer) VALUES ('new', 'bronze')",
  },
  rights_source: {
    group: "rights",
    insert: "(source_key, provider, category) VALUES ('new', 'proof', 'proof')",
  },
  target_set: {
    group: "config",
    insert: "(kind, name) VALUES ('new', 'proof')",
  },
  target: {
    group: "config",
    insert: `(target_set_id, platform) VALUES (${ref("target_set")}, 'proof')`,
  },
  target_spec: {
    group: "config",
    insert: `(target_id, resource_kind, canonical_key) VALUES (${ref("target")}, 'account', 'proof')`,
  },
  host_health: { group: "host", insert: "(host) VALUES ('new')" },
  dbt_job: {
    group: "config",
    insert:
      "(job_id, runner, cadence, scope) VALUES ('new', 'core', 'proof', 'global')",
  },
  runbook: {
    group: "config",
    insert: "(slug, title, body_md) VALUES ('new', 'proof', 'proof')",
  },
  audit_log: {
    group: "config",
    insert: "(actor, action, subject) VALUES ('proof', 'proof', 'proof')",
  },
  workbench_session: {
    group: "config",
    insert:
      "(user_id, scratch_schema, expires_at) VALUES ('proof', 'wb_new', now())",
  },
  cycle: {
    group: "ledger",
    insert:
      "(cadence, scope, opened_by_dbt_run_id) VALUES ('proof', 'global', 'proof')",
  },
  cycle_attempt: {
    group: "ledger",
    insert: `(dbt_run_id, cycle_id, reason_category, git_sha, image_digest) VALUES ('new', ${ref("cycle")}, 'proof', 'proof', 'proof')`,
  },
  cycle_input: {
    group: "ledger",
    insert: `(cycle_id, dump_id, phase) VALUES (${ref("cycle")}, ${ref("dump")}, 'derived')`,
  },
  scope_close: { group: "ledger", insert: "(scope) VALUES ('new')" },
  // The Core runner writes it through control_rt, like its dbt_job registration.
  runner_restore: {
    group: "config",
    insert: `(lock_key, cycle_id, target) VALUES ('new', ${ref("cycle")}, 'pg')`,
  },
  target_export: {
    group: "ledger",
    insert: `(cycle_id, target_set_id, member_count) VALUES (${ref("cycle")}, ${ref("target_set")}, 0)`,
  },
  target_export_member: {
    group: "ledger",
    insert: `(revision_id, target_id) VALUES (${ref("target_export")}, ${ref("target")})`,
  },
  run: {
    group: "ledger",
    insert: `(kind, work_key, scope, warehouse_id) VALUES ('invoke', 'new', 'global', ${ref("warehouse")})`,
  },
  run_attempt: {
    group: "ledger",
    insert: `(run_id, attempt_no, deadline_at) VALUES (${ref("run")}, 1, now())`,
  },
  batch: {
    group: "ledger",
    insert: `(run_id, index, target_ids, dump_ids) VALUES (${ref("run")}, 1, ARRAY[${ref("target")}], ARRAY[${ref("dump")}])`,
  },
  run_event: {
    group: "ledger",
    insert: `(run_id, level, event_type, message) VALUES (${ref("run")}, 'proof', 'proof', 'proof')`,
  },
  cursor: {
    group: "cursor",
    insert: `(streamline_id, cursor_key) VALUES (${ref("streamline")}, 'new')`,
  },
  dump: {
    group: "ledger",
    insert: `(kind, run_id, uri_prefix) VALUES ('output', ${ref("run")}, 'proof')`,
  },
  load: {
    group: "ledger",
    insert: `(dump_id, warehouse_id, target_table) VALUES (${ref("dump")}, ${ref("warehouse")}, 'proof')`,
  },
  dead_letter: {
    group: "ledger",
    insert: `(run_id, reason) VALUES (${ref("run")}, 'proof')`,
  },
  budget_reservation: {
    group: "ledger",
    insert: `(budget_id, run_id, policy_snapshot) VALUES (${ref("budget")}, ${ref("run")}, '{}')`,
  },
  cost_ledger: {
    group: "ledger",
    insert:
      "(vendor, provider_request_id, unit, quantity, cost_cents, origin) VALUES ('proof', 'proof', 'proof', 0, 0, 'estimate')",
  },
  call_ledger: {
    group: "ledger",
    insert: `(run_id, vendor, endpoint, attempt) VALUES (${ref("run")}, 'proof', 'proof', 1)`,
  },
  alert: {
    group: "alert",
    insert:
      "(class, severity, subject_type, subject_id, opened_at) VALUES ('proof', 'info', 'proof', 'proof', now())",
  },
  reference_source: { group: "reference", insert: "(source) VALUES ('new')" },
  workbench_run: {
    group: "ledger",
    insert: `(session_id, kind, input) VALUES (${ref("workbench_session")}, 'query', '{}')`,
  },
} satisfies Record<string, Policy>;
function allowed(
  role: string,
  group: Group,
  operation: string,
  column = "",
  table = "",
): boolean {
  if (role === "migrator") return true;
  if (group === "showcase") return role === "control_rt";
  if (group === "showcase_actor")
    return role === "control_rt" && operation !== "DELETE";
  if (group === "showcase_call")
    return (
      role === "control_rt" &&
      (operation === "SELECT" ||
        operation === "INSERT" ||
        (operation === "UPDATE" &&
          ["undone_at", "hidden_at", "hidden_by"].includes(column)))
    );
  // The data API's key check reads keys and tenants and nothing else.
  if (role === "api_key_reader")
    return operation === "SELECT" && ["api_key", "tenant"].includes(table);
  // api_key hashes are readable and writable only by the control plane and the migrator; the service never reads keys
  if (group === "api_key") return role === "control_rt" || role === "migrator";
  if (group === "host" && operation !== "SELECT") {
    if (role === "rights_sync" || operation === "DELETE") return false;
    const columns = role === "control_rt" ? hostPolicy : hostObserved;
    return (
      column === "" ||
      (columns.includes(column) &&
        (operation === "INSERT" || column !== "host"))
    );
  }
  if (operation === "SELECT")
    return role !== "rights_sync" || group === "rights";
  if (role === "rights_sync")
    return group === "rights" && ["INSERT", "UPDATE"].includes(operation);
  if (role === "control_rt") {
    if (group === "config") return true;
    if (group === "prompt") return operation === "INSERT";
    return (
      operation === "UPDATE" &&
      ((group === "streamline" &&
        (knobs.includes(column) || transport.includes(column))) ||
        (group === "alert" && acknowledgement.includes(column)) ||
        (group === "reference" && reimportRequest.includes(column)) ||
        (group === "cursor" && column === "reset_generation"))
    );
  }
  if (operation === "DELETE") return false;
  if (group === "ledger" || group === "reference") return true;
  if (group === "cursor") {
    if (operation === "INSERT") return column !== "reset_generation";
    return (
      operation === "UPDATE" &&
      ["", "cursor_value", "version", "dump_id", "updated_at"].includes(column)
    );
  }
  if (group === "streamline") return column === "" || registry.includes(column);
  if (group === "alert") {
    if (column === "attempt_no") return operation === "INSERT";
    return column === "" || alertFacts.includes(column);
  }
  return false;
}
const lines: string[] = [];
let failures = 0;
function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}
function code(error: unknown): string {
  return error instanceof postgres.PostgresError
    ? error.code
    : "unexpected-error";
}
function record(role: string, summary: string, expected: string, got: string) {
  if (expected !== got) failures++;
  const line = `${expected === got ? "OK" : "FAIL"} ${role} ${summary} expected=${expected} got=${got}`;
  lines.push(line);
  console.log(line);
}
// Optional overrides place the disposable proof database and the warehouse checks on a shared cluster.
const identifier = (value: string) => {
  if (!/^[a-z_][a-z0-9_]*$/.test(value))
    throw new Error(`Invalid database identifier ${value}`);
  return value;
};
const database = `${identifier(process.env.MDP_GRANTS_PROOF_PREFIX ?? "control_proof")}_${Date.now()}`;
const warehouseDatabase = identifier(
  process.env.MDP_GRANTS_WAREHOUSE_DATABASE ?? "warehouse",
);
const clients: ReturnType<typeof postgres>[] = [];
let admin: ReturnType<typeof postgres> | undefined;
let created = false;
let interrupted = false;
// Do not exit in signal handlers: unwind through the same finally/teardown path.
const interrupt = () => {
  interrupted = true;
};
process.on("SIGINT", interrupt);
process.on("SIGTERM", interrupt);
function connection(rawUrl: string, db: string) {
  if (db === "control") throw new Error("Proof must never connect to control");
  const url = new URL(rawUrl);
  url.pathname = `/${db}`;
  const client = postgres(url.toString(), {
    max: 1,
    connect_timeout: 5,
    onnotice: () => {},
  });
  clients.push(client);
  return client;
}
async function check(
  client: ReturnType<typeof postgres>,
  role: string,
  label: string,
  statement: string,
  expected = "success",
) {
  if (interrupted) throw new Error("Proof interrupted");
  let got = "success";
  try {
    await client.unsafe("BEGIN");
    try {
      await client.unsafe(statement);
    } finally {
      await client.unsafe("ROLLBACK");
    }
  } catch (error) {
    got = code(error);
  }
  record(role, label, expected, got);
}
try {
  const urls = new Map(
    [...controlRoles, ...warehouseRoles].map((role) => [
      role,
      required(`MDP_${role.toUpperCase()}_DATABASE_URL`),
    ]),
  );
  const roleUrl = (role: string) => {
    const url = urls.get(role);
    if (!url) throw new Error(`Missing URL for ${role}`);
    return url;
  };
  admin = connection(required("MDP_CONTROL_ADMIN_URL"), "postgres");
  await admin.unsafe(`CREATE DATABASE ${database} OWNER migrator`);
  created = true;
  await admin.unsafe(`REVOKE ALL ON DATABASE ${database} FROM PUBLIC`);
  await admin.unsafe(
    `GRANT CONNECT ON DATABASE ${database} TO migrator, rights_sync, control_rt, functions_rt, api_key_reader`,
  );
  const owner = connection(roleUrl("migrator"), database);
  await migrate(drizzle(owner), {
    migrationsFolder: fileURLToPath(new URL("../drizzle/", import.meta.url)),
  });
  record("harness", "migrate disposable database", "success", "success");
  await owner.begin(async (tx) => {
    await tx`INSERT INTO control.warehouse (adapter, database, dsn_secret_ref, is_production)
      VALUES ('postgres', 'warehouse', 'MDP_WAREHOUSE_URL', true)`;
    // Only prerequisite fixtures; every actual permission check rolls back.
    await tx`INSERT INTO control.tenant (slug, name) VALUES ('fixture', 'proof')`;
    await tx`INSERT INTO control.streamline (source_key, layer) VALUES ('fixture', 'bronze')`;
    await tx`INSERT INTO control.target_set (kind, name) VALUES ('fixture', 'proof')`;
    await tx.unsafe(`INSERT INTO control.target ${policies.target.insert}`);
    await tx.unsafe(`INSERT INTO control.budget ${policies.budget.insert}`);
    await tx.unsafe(
      `INSERT INTO control.workbench_session ${policies.workbench_session.insert.replace("wb_new", "wb_fixture")}`,
    );
    await tx.unsafe(`INSERT INTO control.cycle ${policies.cycle.insert}`);
    await tx.unsafe(
      `INSERT INTO control.run ${policies.run.insert.replace("'new'", "'fixture'")}`,
    );
    await tx.unsafe(`INSERT INTO control.dump ${policies.dump.insert}`);
    // A second cycle leaves the real export/manifest INSERTs free of unique conflicts.
    await tx`WITH extra_cycle AS (
      INSERT INTO control.cycle (cadence, scope, opened_by_dbt_run_id)
      VALUES ('fixture-extra', 'global', 'fixture-extra') RETURNING id
    ) INSERT INTO control.target_export (cycle_id, target_set_id, member_count)
      SELECT id, (SELECT id FROM control.target_set LIMIT 1), 0 FROM extra_cycle`;
    await tx.unsafe(`INSERT INTO control.alert ${policies.alert.insert}`);
  });
  const columns = await owner<{ table_name: string; column_name: string }[]>`
    SELECT table_name, column_name FROM information_schema.columns
    WHERE table_schema = 'control' ORDER BY table_name, ordinal_position`;
  const tableNames = [...new Set(columns.map((row) => row.table_name))].sort();
  record(
    "harness",
    "matrix covers every control table",
    JSON.stringify(Object.keys(policies).sort()),
    JSON.stringify(tableNames),
  );
  for (const role of controlRoles) {
    const client = connection(roleUrl(role), database);
    for (const [table, policy] of Object.entries(policies)) {
      const expected = (operation: string, column = "") =>
        allowed(role, policy.group, operation, column, table)
          ? "success"
          : "42501";
      await check(
        client,
        role,
        `SELECT ${table}`,
        `SELECT * FROM control.${table}`,
        expected("SELECT"),
      );
      await check(
        client,
        role,
        `INSERT ${table}`,
        `INSERT INTO control.${table} ${policy.insert}`,
        expected("INSERT"),
      );
      await check(
        client,
        role,
        `DELETE ${table}`,
        `DELETE FROM control.${table} WHERE false`,
        expected("DELETE"),
      );
      for (const { column_name: column } of columns.filter(
        (row) => row.table_name === table,
      )) {
        // Zero-row column probes isolate ACLs from defaults, FK and uniqueness failures.
        // Each allowed table also receives a real INSERT above.
        await check(
          client,
          role,
          `INSERT ${table}.${column}`,
          `INSERT INTO control.${table} (${column}) SELECT ${column} FROM control.${table} WHERE false`,
          expected("INSERT", column),
        );
        await check(
          client,
          role,
          `UPDATE ${table}.${column}`,
          `UPDATE control.${table} SET ${column} = ${column} WHERE false`,
          expected("UPDATE", column),
        );
      }
      await check(
        client,
        role,
        `ALTER ${table}`,
        `ALTER TABLE control.${table} ADD COLUMN proof_ddl integer`,
        role === "migrator" ? "success" : "42501",
      );
    }
    await check(
      client,
      role,
      "CREATE control table",
      "CREATE TABLE control.proof_ddl (id integer)",
      role === "migrator" ? "success" : "42501",
    );
    await check(
      client,
      role,
      "NEXTVAL landed_seq",
      "SELECT nextval('control.landed_seq')",
      ["migrator", "functions_rt"].includes(role) ? "success" : "42501",
    );
    await check(
      client,
      role,
      "SELECT landed_seq",
      "SELECT last_value FROM control.landed_seq",
      role === "migrator" ? "success" : "42501",
    );
    await check(
      client,
      role,
      "SETVAL landed_seq",
      "SELECT setval('control.landed_seq', 1)",
      role === "migrator" ? "success" : "42501",
    );
  }
  for (const column of ["target_ids", "dump_ids"]) {
    const validator =
      column === "target_ids"
        ? selectSchemas.batch.shape.target_ids
        : selectSchemas.batch.shape.dump_ids;
    record(
      "schema",
      `batch.${column} rejects non-UUID`,
      "false",
      String(validator.safeParse(["invalid"]).success),
    );
    record(
      "schema",
      `batch.${column} accepts UUID`,
      "true",
      String(
        validator.safeParse(["00000000-0000-4000-8000-000000000001"]).success,
      ),
    );
  }
  await check(
    owner,
    "migrator",
    "zero production rejected when deferred constraints run",
    "UPDATE control.warehouse SET is_production = false; SET CONSTRAINTS ALL IMMEDIATE",
    "23514",
  );
  await check(
    owner,
    "migrator",
    "second production rejected by unique index",
    "INSERT INTO control.warehouse (adapter, database, dsn_secret_ref, is_production) VALUES ('postgres', 'proof', 'MDP_WAREHOUSE_URL', true)",
    "23505",
  );
  await check(
    owner,
    "migrator",
    "production flip satisfies deferred constraints",
    "UPDATE control.warehouse SET is_production = false; INSERT INTO control.warehouse (adapter, database, dsn_secret_ref, is_production) VALUES ('postgres', 'proof', 'MDP_WAREHOUSE_URL', true); SET CONSTRAINTS ALL IMMEDIATE",
  );
  await check(
    owner,
    "migrator",
    "resolved target requires account",
    `INSERT INTO control.target (target_set_id, platform, resolution_status) VALUES (${ref("target_set")}, 'proof', 'resolved')`,
    "23514",
  );
  const control = connection(roleUrl("control_rt"), database);
  await check(
    control,
    "control_rt",
    "session lifecycle real rows",
    `INSERT INTO control.workbench_session ${policies.workbench_session.insert}; UPDATE control.workbench_session SET last_used_at = now() WHERE scratch_schema = 'wb_new'; DELETE FROM control.workbench_session WHERE scratch_schema = 'wb_new'`,
  );
  await check(
    control,
    "control_rt",
    "acknowledge and resolve real alert",
    `UPDATE control.alert SET acknowledged_by = 'proof', resolved_at = now()`,
  );
  await check(
    owner,
    "migrator",
    "musicbrainz reference source seeded",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM control.reference_source WHERE source = 'musicbrainz' AND imported_generation IS NULL) THEN RAISE EXCEPTION 'missing seed'; END IF; END $$",
  );
  await check(
    control,
    "control_rt",
    "request re-import on real row",
    "UPDATE control.reference_source SET reimport_requested_at = now(), reimport_requested_by = 'proof', updated_at = now() WHERE source = 'musicbrainz' RETURNING *",
  );
  await check(
    control,
    "control_rt",
    "write probe state on real row",
    "UPDATE control.reference_source SET imported_generation = 'proof' WHERE source = 'musicbrainz'",
    "42501",
  );
  const functions = connection(roleUrl("functions_rt"), database);
  await check(
    functions,
    "functions_rt",
    "reference probe upsert real row",
    `INSERT INTO control.reference_source (source, imported_generation, mirror_counts, disk_used_bytes) VALUES ('musicbrainz', 'proof', '{"artist": 1}', 1) ON CONFLICT (source) DO UPDATE SET imported_generation = EXCLUDED.imported_generation, mirror_counts = EXCLUDED.mirror_counts, disk_used_bytes = EXCLUDED.disk_used_bytes, updated_at = now()`,
  );
  await check(
    functions,
    "functions_rt",
    "registry knob defaults",
    `INSERT INTO control.streamline ${policies.streamline.insert}; DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM control.streamline WHERE source_key = 'new' AND enabled AND NOT allow_partial AND batch_size = 1 AND max_concurrency = 1 AND timeout_s = 300 AND storage = 'heap' AND acknowledged_fingerprints = '{}') THEN RAISE EXCEPTION 'wrong knob defaults'; END IF; END $$`,
  );
  const rights = connection(roleUrl("rights_sync"), database);
  await check(
    rights,
    "rights_sync",
    "UPSERT rights_source",
    `INSERT INTO control.rights_source ${policies.rights_source.insert}; INSERT INTO control.rights_source ${policies.rights_source.insert} ON CONFLICT (source_key) DO UPDATE SET provider = EXCLUDED.provider`,
  );
  await check(
    owner,
    "migrator",
    "future SELECT defaults",
    `CREATE TABLE control.proof_defaults (id integer); DO $$ BEGIN IF NOT has_table_privilege('control_rt', 'control.proof_defaults', 'SELECT') OR NOT has_table_privilege('functions_rt', 'control.proof_defaults', 'SELECT') OR has_table_privilege('rights_sync', 'control.proof_defaults', 'SELECT') OR has_table_privilege('api_key_reader', 'control.proof_defaults', 'SELECT') THEN RAISE EXCEPTION 'wrong defaults'; END IF; END $$`,
  );
  for (const role of warehouseRoles) {
    const denied = connection(roleUrl(role), database);
    await check(
      denied,
      role,
      "CONNECT isolated control database",
      "SELECT 1",
      "42501",
    );
    const warehouse = connection(roleUrl(role), warehouseDatabase);
    await check(warehouse, role, "CONNECT warehouse", "SELECT 1");
    // dbt_transform owns its schemas and workbench_wh creates wb_*; the other warehouse roles cannot create schemas
    // dbt_transform owns its schemas; workbench_wh may create only wb_* (database-enforced guard); the other warehouse roles cannot create schemas
    await check(
      warehouse,
      role,
      "warehouse CREATE SCHEMA (non-wb name)",
      "CREATE SCHEMA proof_forbidden",
      role === "dbt_transform" ? "success" : "42501",
    );
    if (role === "workbench_wh")
      await check(
        warehouse,
        role,
        "warehouse CREATE SCHEMA wb_*",
        "CREATE SCHEMA wb_proof_probe",
        "success",
      );
    await check(
      warehouse,
      role,
      "warehouse forbidden public DDL",
      "CREATE TABLE public.proof_forbidden (id integer)",
      "42501",
    );
    // dbt's delete+insert incremental strategy stages batches in temporary tables; readers never create them.
    if (["dbt_transform", "service_read", "reader_wh"].includes(role)) {
      await check(
        warehouse,
        role,
        "warehouse CREATE TEMP TABLE",
        "CREATE TEMP TABLE proof_temp (id integer)",
        role === "dbt_transform" ? "success" : "42501",
      );
    }
  }
  // reader_wh reads marts and tenant_*_marts only: a tenant build's staging, intermediate and dbt
  // tables stay out of its reach, and no privilege anywhere else names it: not a schema, a table or a column
  // grant outside raw, marts and tenant_*_marts (raw holds its lineage columns), and no default privilege.
  // The initial schemas exist before any dbt table: both schema USAGE and future table SELECT matter.
  const warehouseAdmin = connection(
    required("MDP_CONTROL_ADMIN_URL"),
    warehouseDatabase,
  );
  for (const schema of ["staging", "intermediate", "marts"]) {
    for (const role of ["service_read", "workbench_wh"]) {
      const readableSchema =
        role === "workbench_wh" ? `explore_${schema}` : schema;
      await check(
        warehouseAdmin,
        role,
        `${schema}: read first dbt table through permitted path`,
        `SET LOCAL ROLE dbt_transform; CREATE TABLE ${schema}.proof_initial_grants (id integer); SET LOCAL ROLE ${role}; SELECT * FROM ${readableSchema}.proof_initial_grants`,
      );
    }
    await check(
      warehouseAdmin,
      "workbench_wh",
      `${schema}: deny undeclared original columns`,
      `SET LOCAL ROLE dbt_transform; CREATE TABLE ${schema}.proof_initial_grants (id integer); SET LOCAL ROLE workbench_wh; SELECT * FROM ${schema}.proof_initial_grants`,
      "42501",
    );
  }
  const dbt = connection(roleUrl("dbt_transform"), warehouseDatabase);
  const layers = ["staging", "intermediate", "dbt", "marts"]
    .map(
      (layer) =>
        `CREATE SCHEMA tenant_proof_${layer}; CREATE TABLE tenant_proof_${layer}.proof (id integer)`,
    )
    .join("; ");
  await check(
    dbt,
    "dbt_transform",
    "tenant build grants reader_wh tenant marts only",
    `${layers}; DO $$ BEGIN
    IF NOT has_table_privilege('reader_wh', 'tenant_proof_marts.proof', 'SELECT')
      OR has_schema_privilege('reader_wh', 'tenant_proof_staging', 'USAGE') OR has_any_column_privilege('reader_wh', 'tenant_proof_staging.proof', 'SELECT')
      OR has_schema_privilege('reader_wh', 'tenant_proof_intermediate', 'USAGE') OR has_any_column_privilege('reader_wh', 'tenant_proof_intermediate.proof', 'SELECT')
      OR has_schema_privilege('reader_wh', 'tenant_proof_dbt', 'USAGE') OR has_any_column_privilege('reader_wh', 'tenant_proof_dbt.proof', 'SELECT')
    THEN RAISE EXCEPTION 'reader_wh grant outside tenant marts'; END IF; END $$`,
  );
  const reader = connection(roleUrl("reader_wh"), warehouseDatabase);
  await check(
    reader,
    "reader_wh",
    "no grant in any tenant staging or intermediate schema",
    `DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace n WHERE (n.nspname LIKE 'tenant\\_%\\_staging' OR n.nspname LIKE 'tenant\\_%\\_intermediate')
      AND (has_schema_privilege('reader_wh', n.oid, 'USAGE')
        OR EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid AND has_any_column_privilege('reader_wh', c.oid, 'SELECT'))))
    THEN RAISE EXCEPTION 'reader_wh grant in a tenant staging or intermediate schema'; END IF; END $$`,
  );
  await check(
    reader,
    "reader_wh",
    "no schema, table or column grant outside raw, marts and tenant_*_marts",
    `DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT IN ('raw', 'marts') AND n.nspname NOT LIKE 'tenant\\_%\\_marts'
      AND (EXISTS (SELECT 1 FROM aclexplode(n.nspacl) a WHERE a.grantee = 'reader_wh'::regrole)
        OR EXISTS (SELECT 1 FROM pg_class c, aclexplode(c.relacl) a WHERE c.relnamespace = n.oid AND a.grantee = 'reader_wh'::regrole)
        OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_attribute t ON t.attrelid = c.oid, aclexplode(t.attacl) a
                   WHERE c.relnamespace = n.oid AND a.grantee = 'reader_wh'::regrole)))
    THEN RAISE EXCEPTION 'reader_wh privilege outside raw, marts and tenant marts'; END IF; END $$`,
  );
  await check(
    reader,
    "reader_wh",
    "no default privilege names reader_wh",
    `DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_default_acl d, aclexplode(d.defaclacl) a WHERE a.grantee = 'reader_wh'::regrole)
    THEN RAISE EXCEPTION 'a default privilege names reader_wh'; END IF; END $$`,
  );
} catch (error) {
  record(
    "harness",
    interrupted ? "interrupted" : "setup/execution",
    "success",
    error instanceof Error && error.message.startsWith("Missing ")
      ? error.message
      : code(error),
  );
} finally {
  await Promise.allSettled(
    clients
      .filter((client) => client !== admin)
      .map((client) => client.end({ timeout: 5 })),
  );
  if (admin && created) {
    try {
      await admin.unsafe(`DROP DATABASE ${database} WITH (FORCE)`);
      record("harness", "DROP disposable database", "success", "success");
    } catch (error) {
      record("harness", "DROP disposable database", "success", code(error));
    }
  }
  await admin?.end({ timeout: 5 });
  process.off("SIGINT", interrupt);
  process.off("SIGTERM", interrupt);
  const dir = fileURLToPath(
    new URL("../../../../ops/evidence/platform/", import.meta.url),
  );
  await mkdir(dir, { recursive: true });
  const stamp = new Date().toISOString().replaceAll(":", "-");
  const summary = `SUMMARY checks=${lines.length} failures=${failures}`;
  const evidence = `EVIDENCE ops/evidence/platform/grants-${stamp}.txt`;
  console.log(summary);
  console.log(evidence);
  await writeFile(
    `${dir}grants-${stamp}.txt`,
    [...lines, summary, evidence].join("\n") + "\n",
  );
}
if (failures) process.exitCode = 1;
