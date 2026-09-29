import { z } from "zod";
import {
  inputQuery,
  permittedInput,
  readableInputs,
  relationFromName,
  workbenchLink,
  type WorkbenchInput,
} from "./workbench-inputs.js";
import { AppError } from "./db.js";
import { Hono } from "hono";
import { createRouterClient } from "@orpc/server";
import { router, type Context } from "./router.js";
import { Layout } from "./pages.js";
import {
  CopyChip,
  EntityName,
  EntityPopover,
  RelativeTime,
} from "./primitives.js";
import { relationLabels, sandboxFacts } from "./console-semantics.js";
import policy from "./sandbox-policy.json" with { type: "json" };

export const sandboxPages = new Hono<{ Variables: { context: Context } }>();
type Row = Record<string, unknown>;
const records = (value: unknown): Row[] =>
  z.array(z.record(z.string(), z.unknown())).catch([]).parse(value);
const command = (row: Row, action: string) =>
  `uv run --project functions python ops/fly/postgres/analyst-account.py ${action} ${String(row.owner_role).replace(/^(analyst_|explorer_)/, "")}${row.is_explorer ? " --explore" : ""}`;
export function SandboxPage({
  rows,
  selected,
  operator = false,
  message,
  admin = true,
  inputs = [],
}: {
  rows: Row[];
  selected?: string | undefined;
  operator?: boolean;
  admin?: boolean;
  message?: string | undefined;
  inputs?: WorkbenchInput[];
}) {
  const chosen = rows.filter(
    (row) => operator || String(row.schema_name) === selected,
  );
  return (
    <Layout title={operator ? "Sandboxes" : "Sandbox"} subtitle="">
      <style>{`.sandbox-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:20px}.sandbox-grid article{min-width:0}.sandbox-grid pre,.sandbox-grid code{white-space:pre-wrap;overflow-wrap:anywhere}.sandbox-grid .tools{display:flex;gap:8px;flex-wrap:wrap}.sandbox-grid dl{display:grid;grid-template-columns:1fr 1fr;gap:8px}.sandbox-grid dd{margin:0;overflow-wrap:anywhere}.sandbox-grid ul{padding-left:18px}.sandbox-grid li{overflow-wrap:anywhere}`}</style>
      <div class="tools">
        <a href="/sandboxes">Sandbox</a> ·{" "}
        {admin ? (
          <a href="/sandboxes?operator=1">Operator view</a>
        ) : (
          <span>
            Operator view and archive need admin. Ask an operator;{" "}
            <a href="/runbooks/forbidden">open the access runbook</a>.
          </span>
        )}{" "}
        · <a href="/workbench">Workbench →</a>
      </div>
      {message && <p role="status">{message}</p>}
      {!admin && (
        <section class="card">
          <p>
            Sandbox changes need admin. Ask an operator;{" "}
            <a href="/runbooks/forbidden">open the access runbook</a>.
          </p>
          <fieldset disabled>
            <button>Freeze</button>
            <button>Unfreeze</button>
            <button>Disable</button>
            <button>Archive</button>
          </fieldset>
        </section>
      )}
      <section class="card">
        <h2>{operator ? "Logins" : "Login"}</h2>
        <p>
          {operator
            ? "Check the owner and dependents before using a control or copied command."
            : "Select your SQL login’s schema; use that login in notebooks to save data."}
        </p>
        {!operator && (
          <form method="get">
            <label>
              Sandbox{" "}
              <select name="schema">
                <option value="">Choose a schema</option>
                {rows.map((row) => (
                  <option
                    value={String(row.schema_name)}
                    selected={row.schema_name === selected}
                  >
                    {String(row.schema_name)} · {String(row.owner_role)}
                  </option>
                ))}
              </select>
            </label>
            <button>Show</button>
          </form>
        )}
        {!rows.length && <p>{policy.messages.sandbox_missing}</p>}
        <p>
          {policy.connection_limit} connections · {policy.statement_timeout}{" "}
          query limit · New objects freeze at {policy.quota_bytes / 1073741824}{" "}
          GiB.
        </p>
        <p>Remove unused tables and refresh status to free space.</p>
      </section>
      <div class="sandbox-grid">
        {chosen.map((row) => {
          const schema = String(row.schema_name),
            facts = sandboxFacts(row),
            labels = relationLabels(schema + ".*");
          return (
            <article class="card">
              <h2>
                <EntityName slug={schema} />
              </h2>
              <p>
                <span class="badge">{labels.layer}</span>{" "}
                <span class="badge">{facts.state}</span>
              </p>
              <dl>
                <dt>Owner</dt>
                <dd>{String(row.owner_role)}</dd>
                <dt>Team read</dt>
                <dd>{String(row.sharing)}</dd>
                <dt>Storage</dt>
                <dd>
                  {facts.size} / {facts.quota} ({facts.percent}%)
                </dd>
                <dt>Objects</dt>
                <dd>{String(row.object_count)}</dd>
                <dt>Last used</dt>
                <dd>
                  {row.last_use ? (
                    <RelativeTime at={String(row.last_use)} />
                  ) : (
                    "Not recorded"
                  )}
                </dd>
              </dl>
              {records(row.notices).map((n) => (
                <p role="alert">{String(n.message)}</p>
              ))}
              <h3>Objects</h3>
              <ul>
                {records(row.objects).map((obj) => {
                  let source: string[] = [];
                  try {
                    source = z
                      .object({ derived_from: z.array(z.string()).default([]) })
                      .parse(
                        JSON.parse(String(obj.labels ?? "{}")),
                      ).derived_from;
                  } catch {}
                  const derived = source.length ? source.join(", ") : "unknown";
                  const readable = source
                    .map(relationFromName)
                    .flatMap((relation) =>
                      relation ? permittedInput(relation, inputs) || [] : [],
                    )[0];
                  return (
                    <li>
                      <EntityPopover
                        summary={String(obj.name)}
                        label={<EntityName slug={String(obj.name)} />}
                        facts={{
                          format: String(obj.kind) === "v" ? "view" : "table",
                          size: `${Number(obj.bytes)} bytes`,
                          details: [{ label: "Source", value: derived }],
                          links: readable
                            ? [
                                {
                                  label: "Open source in Workbench",
                                  href: workbenchLink(readable),
                                },
                              ]
                            : [{ label: "Open Explorer", href: "/explorer" }],
                        }}
                      />
                      <p>Source: {derived}</p>
                      <p>
                        Read this saved table through its owner’s notebook
                        connection.
                      </p>
                      {readable && (
                        <CopyChip
                          value={inputQuery(readable)}
                          label="Copy source SELECT"
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
              {!records(row.objects).length && (
                <p>
                  No objects yet. Copy a served mart with CREATE TABLE in your
                  notebook.
                </p>
              )}
              <h3>Dependents</h3>
              {records(row.dependents).length ? (
                <ul>
                  {records(row.dependents).map((d) => (
                    <li>
                      {String(d.relation)} · {String(d.owner)}. Tell this owner
                      before archiving.
                    </li>
                  ))}
                </ul>
              ) : (
                <p>
                  No saved dependents recorded; check notebooks before
                  archiving.
                </p>
              )}
              <CopyChip
                value={`pnpm --dir control mdp warehouse sandbox status --schema ${schema}`}
                label="Copy status command"
              />
              {operator && (
                <>
                  <h3>Access</h3>
                  <div class="tools">
                    {(["freeze", "unfreeze", "disable"] as const).map(
                      (action) => (
                        <form method="post">
                          <input type="hidden" name="schema" value={schema} />
                          <button name="action" value={action}>
                            {action === "unfreeze"
                              ? "Unfreeze"
                              : action === "freeze"
                                ? "Freeze new objects"
                                : "Disable login"}
                          </button>
                        </form>
                      ),
                    )}
                  </div>
                  <p>
                    Rotation ends sessions; send the new password privately and
                    ask the owner to reconnect.
                  </p>
                  <CopyChip
                    value={command(row, "rotate")}
                    label="Copy rotate command"
                  />
                  <p>
                    Archive disables the login and saves Parquet under{" "}
                    {policy.archive_root}.
                  </p>
                  <p>
                    The schema is removed only when no external objects depend
                    on it.
                  </p>
                  <p>
                    Run from your operator checkout, then open archive.json.
                  </p>
                  <CopyChip
                    value={command(row, "archive")}
                    label="Copy archive command"
                  />
                  <p>
                    Keep work online with <code>--transfer analyst_handle</code>
                    ; tenant copies need an explorer recipient.
                  </p>
                  <p>Ask the new owner to check status.</p>
                </>
              )}
            </article>
          );
        })}
      </div>
    </Layout>
  );
}
sandboxPages.get("/", async (c) => {
  const admin = c.get("context").identity.admin;
  if (!admin && c.req.query("operator") === "1")
    throw new AppError(
      "forbidden",
      "Sandbox operator view needs the admin role. Ask an operator; open the access runbook.",
      403,
    );
  const client = createRouterClient(router, { context: c.get("context") });
  const data = await client.workbench.sandboxStatus({});
  return c.html(
    <SandboxPage
      rows={data.sandboxes}
      inputs={await readableInputs(admin)}
      admin={admin}
      message={
        !admin && !c.get("context").identity.warehouse_role
          ? "No warehouse login is linked. Ask an operator to issue a staff key with --warehouse-role analyst_<handle>; open the access runbook above."
          : undefined
      }
      selected={
        admin
          ? c.req.query("schema")
          : String(data.sandboxes[0]?.schema_name ?? "")
      }
      operator={c.req.query("operator") === "1"}
    />,
  );
});
sandboxPages.post("/", async (c) => {
  const body = await c.req.parseBody();
  const client = createRouterClient(router, { context: c.get("context") });
  const action = z
    .enum(["freeze", "unfreeze", "disable"])
    .safeParse(body.action);
  if (!action.success)
    return c.text(
      "Choose freeze, unfreeze or disable, then submit again.",
      400,
    );
  const result = await client.workbench.sandboxAction({
    schema: String(body.schema),
    action: action.data,
  });
  const data = await client.workbench.sandboxStatus({});
  return c.html(
    <SandboxPage
      rows={data.sandboxes}
      inputs={await readableInputs(true)}
      operator
      message={result.message}
    />,
  );
});
