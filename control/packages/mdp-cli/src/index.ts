import { errorHint } from "@mdp/contracts";
import { formatStatus } from "./status.js";
import {
  readFileSync,
  writeFileSync,
  readdirSync,
  mkdirSync,
  existsSync,
} from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { createORPCClient } from "@orpc/client";
import { RPCLink } from "@orpc/client/fetch";
import type { ContractRouterClient } from "@orpc/contract";
import { apiKeyCreateInput, contract } from "@mdp/contracts";
import { z } from "zod";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const name = z.string().regex(/^[a-z][a-z0-9_]*$/);
export { scaffold } from "./scaffold.js";
import { scaffold } from "./scaffold.js";

function flag(args: string[], name: string) {
  const i = args.indexOf(name);
  return i < 0 ? undefined : args[i + 1];
}
export const knobsUsage = "pnpm --dir control mdp knobs set <source_key> --enable|--disable [--batch-size n] [--max-concurrency n]";

export async function knobs(
  client: Pick<ContractRouterClient<typeof contract>, "streamlines">,
  action: string | undefined,
  args: string[],
) {
  const [source, ...options] = args;
  if (action !== "set" || !source || !name.safeParse(source).success) {
    throw new Error(`Choose a source. Run ${knobsUsage}`);
  }
  let enabled: boolean | undefined;
  let batch_size: number | undefined;
  let max_concurrency: number | undefined;
  const seen = new Set<string>();
  for (let index = 0; index < options.length; index += 1) {
    const option = options[index]!;
    if (seen.has(option)) throw new Error(`Repeated option. Run ${knobsUsage}`);
    seen.add(option);
    if (option === "--enable" || option === "--disable") {
      if (enabled !== undefined) throw new Error(`Choose enable or disable. Run ${knobsUsage}`);
      enabled = option === "--enable";
    } else if (option === "--batch-size" || option === "--max-concurrency") {
      const value = options[++index];
      if (!value || !/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value))) {
        throw new Error(`Use a positive whole number for ${option}. Run ${knobsUsage}`);
      }
      if (option === "--batch-size") batch_size = Number(value);
      else max_concurrency = Number(value);
    } else {
      throw new Error(`Unknown option. Run ${knobsUsage}`);
    }
  }
  if (enabled === undefined) throw new Error(`Choose enable or disable. Run ${knobsUsage}`);
  const result = await client.streamlines.patchKnobs({ source_key: source, enabled, batch_size, max_concurrency });
  console.log(JSON.stringify(result, null, 2));
  console.log(`Check the source: pnpm --dir control mdp status ${source}`);
}

export async function tenants(client: ContractRouterClient<typeof contract>, action: string | undefined, args: string[]) {
  const usage = "Usage: pnpm --dir control mdp tenants create --slug <slug> --name <name> | tenants list | tenants patch --tenant <slug> [--name <name>] [--status active|inactive]";
  if (action === "list") return console.log(JSON.stringify(await client.tenants.list({}), null, 2));
  const name = flag(args, "--name");
  if (action === "create") {
    const slug = flag(args, "--slug");
    if (!slug || !name?.trim()) throw new Error(usage);
    return console.log(JSON.stringify(await client.tenants.create({ slug, name }), null, 2));
  }
  if (action === "patch") {
    const slug = flag(args, "--tenant");
    const status = flag(args, "--status");
    if (!slug || (name === undefined && status === undefined) || (name !== undefined && !name.trim())) throw new Error(usage);
    if (status !== undefined && status !== "active" && status !== "inactive") throw new Error("Invalid status. Use --status active or --status inactive.");
    const tenant = (await client.tenants.list({})).find((t) => t.slug === slug);
    if (!tenant) throw new Error(`Tenant ${slug} does not exist. Run pnpm --dir control mdp tenants list to check the slug.`);
    return console.log(JSON.stringify(await client.tenants.patch({ id: tenant.id, name, status }), null, 2));
  }
  throw new Error(usage);
}
// A refused option carries its own next step in the message; the CLI prints it without the control-API hint.
export class UsageError extends Error {}
const keysUsage =
  "Usage: pnpm --dir control mdp keys create --tenant <slug> | keys create --role reader --global [--label <text>] [--expires <iso>] | keys create --role staff --label <text> [--warehouse-role analyst_<handle>] | keys list | keys revoke <id>";
// What to fix when one option is malformed, keyed by the contract field it fills.
const keyOptionHints: Record<string, string> = {
  tenant_slug: "Check --tenant. Run pnpm --dir control mdp tenants list to see the slugs.",
  role: "Check --role. Use --role reader or --role staff.",
  warehouse_role: "Check --warehouse-role. Use analyst_<handle>, such as analyst_demo.",
  label: "Check --label. Use 1 to 200 characters.",
  expires_at: "Check --expires. Use an ISO time with a zone, such as 2027-01-01T00:00:00Z.",
};
function defaultKeyLabel(tenant: string | undefined, global: boolean, role: string | undefined) {
  if (tenant) return `${tenant} console`;
  if (global) return "global reader";
  return `${role ?? "reader"} console`;
}
// Data-API keys: the secret prints once, on create, and is stored only as a hash.
// A tenant key reads its tenant's marts; a global key (--global) reads global marts.
export async function keys(client: Pick<ContractRouterClient<typeof contract>, "apiKeys">, action: string | undefined, args: string[]) {
  if (action === "create") {
    const tenant_slug = flag(args, "--tenant");
    const global = args.includes("--global");
    const role = flag(args, "--role");
    const input = apiKeyCreateInput.safeParse({
      tenant_slug,
      global,
      role,
      warehouse_role: flag(args, "--warehouse-role"),
      label: flag(args, "--label") ?? defaultKeyLabel(tenant_slug, global, role),
      expires_at: flag(args, "--expires"),
    });
    if (!input.success) {
      const issue = input.error.issues[0];
      if (issue?.code === "custom") throw new UsageError(issue.message);
      throw new UsageError(keyOptionHints[String(issue?.path[0])] ?? keysUsage);
    }
    const created = await client.apiKeys.create(input.data);
    console.log(JSON.stringify(created, null, 2));
    console.error(
      created.tenant_id === null && created.role === "reader"
        ? "Save api_key now. It is shown only once. For the showcase, store it as MDP_SHOWCASE_READER_KEY: see docs/operating.md#give-a-viewer-access."
        : "Save api_key now. It is shown only once.",
    );
    return;
  }
  if (action === "list") {
    console.log(JSON.stringify(await client.apiKeys.list({ tenant_slug: flag(args, "--tenant"), include_revoked: args.includes("--revoked") }), null, 2));
    return;
  }
  if (action === "revoke") {
    console.log(JSON.stringify(await client.apiKeys.revoke({ id: z.uuid().parse(args[0]) }), null, 2));
    return;
  }
  throw new UsageError(keysUsage);
}
// A local command prints its own reason and next step; the CLI adds no control-API hint for it.
class LocalCommandError extends Error {}
function run(args: string[]) {
  const result = spawnSync(args[0] ?? "", args.slice(1), {
    cwd: root,
    stdio: "inherit",
  });
  if (result.status !== 0) throw new LocalCommandError("Command failed");
}
async function main(args: string[]) {
  const [verb, kind, arg, ...flags] = args;
  if (verb === "warehouse") {
    const result = spawnSync("uv", ["run", "--project", resolve(root, "functions"), "mdp", "warehouse", ...args.slice(1)], { cwd: process.env.INIT_CWD ?? process.cwd(), stdio: "inherit" });
    process.exitCode = result.status ?? 1;
    return;
  }
  if (verb === "scaffold" && kind === "api") return scaffold(name.parse(arg));
  if ((verb === "new" && kind === "llm-step") || (verb === "host" && kind === "unpause")) {
    return run(["uv", "run", "--project", "functions", "mdp", verb, kind, z.string().min(1).parse(arg)]);
  }
  if (verb === "new" && kind === "function") {
    const key = name.parse(arg);
    const layer = z
      .enum(["bronze", "silver", "gold", "universal"])
      .parse(flags[flags.indexOf("--class") + 1]);
    const cadence = z
      .enum(["hourly", "daily", "weekly"])
      .parse(flags[flags.indexOf("--cadence") + 1]);
    const dir = resolve(root, `functions/src/mdp_functions/sources/${key}`);
    if (existsSync(dir)) throw new Error("Function already exists");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      resolve(dir, "function.py"),
      `"""${key}: implement the declared function before enabling it."""\nfrom mdp_functions.layers import ${layer}\n\n@${layer}(source_key="${key}", writes=["raw.${key}"], cadence="${cadence}"${layer !== "bronze" ? ", reads=[]" : ""}${["gold", "universal"].includes(layer) ? ", external=True" : ""})\nasync def function(ctx):\n    raise NotImplementedError("Implement this function before deployment")\n    yield {}\n`,
    );
    run(["uv", "run", "--project", "functions", "mdp", "sources", "export"]);
    return;
  }
  if (verb === "new" && kind === "prompt") {
    const key=name.parse(arg);const dir=resolve(root,'control/prompts',key);mkdirSync(dir,{recursive:true});
    const version=1+Math.max(0,...readdirSync(dir).map(p=>Number(p.replace('.md',''))).filter(Number.isFinite));
    const file=resolve(dir,`${version}.md`);writeFileSync(file,'Classify the supplied account features. Return one label: emerging or established.\n',{flag:'wx'});
    console.log(`Created immutable prompt ${key} version ${version}. Open a PR.`);return;
  }
  if (verb === "new" && kind === "mart" && args.includes("--help")) {
    console.log("Usage: pnpm --dir control mdp new mart <mart_name> [--from sandbox_<handle>.<view>]\nCreate a mart contract and SQL file. Fill both files, then run bash ops/ready.sh.");
    return;
  }
  if (verb === "new" && kind === "mart" && flags.includes("--from")) {
    const source = flag(flags, "--from");
    if (!source) throw new Error("--from needs sandbox_<handle>.<view>");
    return run(["uv", "run", "--project", "functions", "python", "-m", "mdp_functions.warehouse_lift", name.parse(arg), "--from", source, "--root", root]);
  }
  if (verb === "new" && kind === "mart") {
    const key = name.parse(arg);
    if (!key.startsWith("mart_"))
      throw new Error("Mart names start with mart_");
    const dir = resolve(root, "dbt/models/marts/global");
    if (existsSync(resolve(dir, `${key}.sql`)))
      throw new Error("Mart already exists");
    writeFileSync(
      resolve(dir, `${key}.yml`),
      `version: 2\nmodels:\n  - name: ${key}\n    config:\n      contract: {enforced: true}\n    columns:\n      - name: id\n        data_type: text\n        constraints: [{type: not_null}]\n`,
    );
    writeFileSync(
      resolve(dir, `${key}.sql`),
      `{{ config(materialized='table', tags=['silver', 'cadence:daily']) }}\nselect cast(null as text) as id where false\n`,
    );
    console.log(`Created contract and SQL stub for ${key}. Complete both, then run bash ops/ready.sh.`);
    return;
  }
  const headers: Record<string, string> = {};
  if (process.env.MDP_API_KEY) headers["x-api-key"] = process.env.MDP_API_KEY;
  else if (process.env.MDP_AUTH_MODE === "dev")
    headers["x-mdp-dev-user"] = "dev-user";
  const client: ContractRouterClient<typeof contract> = createORPCClient(
    new RPCLink({
      url: `${process.env.MDP_CONTROL_API_URL ?? "http://127.0.0.1:8090"}/rpc`,
      headers,
    }),
  );
  if (verb === "probe") {
    if (!kind) throw new Error("Choose a source: pnpm --dir control mdp probe <source> [--scope global]");
    console.log(JSON.stringify(await client.streamlines.dryProbe({source_key:kind,scope:flag(args,"--scope") ?? "global"}),null,2));
    return;
  }
  if (verb === "targets" && kind === "probe") {
    const sets = await client.targets.listSets({});
    const matches = sets.filter((s) => s.id === arg || s.kind === arg || s.name === arg);
    if (matches.length !== 1) throw new Error("Name one target set, or use its id when several sets match");
    console.log(JSON.stringify(await client.targets.probe({ target_set_id: matches[0]!.id }), null, 2));
    return;
  }
  if (verb === "targets" && kind === "reactivate") {
    console.log(JSON.stringify(await client.targets.bulkActivate({ ids: [z.uuid().parse(arg)], active: true }), null, 2));
    return;
  }
  if (verb === "register") {
    console.log(JSON.stringify(await client.functions.register({}), null, 2));
    return;
  }
  if (verb === "platform") {
    const args = [arg, ...flags].filter((v): v is string => v !== undefined);
    if (kind === "sources") {
      console.log(JSON.stringify(await client.platform.sources({}), null, 2));
      return;
    }
    if (kind === "holdings") {
      const since = flag(args, "--since");
      if (!since) throw new Error("Supply a UTC time within the last 90 days. Open /ops/platform to read the past week.");
      console.log(JSON.stringify(await client.platform.holdings({ since }), null, 2));
      return;
    }
    if (kind === "events") {
      console.log(JSON.stringify(await client.platform.events({ after: flag(args, "--after"), limit: Number(flag(args, "--limit") ?? 100) }), null, 2));
      return;
    }
    throw new Error("Choose a read. Run pnpm --dir control mdp platform events.");
  }
  if (verb === "keys") return keys(client, kind, [arg, ...flags].filter((a): a is string => a !== undefined));
  if (verb === "knobs") return knobs(client, kind, [arg, ...flags].filter((a): a is string => a !== undefined));
  if (verb === "showcase") return (await import("./showcase.js")).showcase(client, args.slice(1));
  if (verb === "tenants") return tenants(client, kind, [arg, ...flags].filter((a): a is string => a !== undefined));
  if (verb === "retry" || verb === "replay") {
    const cycle_id = z.uuid().parse(kind);
    console.log(JSON.stringify(await (verb === "retry" ? client.dbt.retry({ cycle_id }) : client.dbt.replay({ cycle_id })), null, 2));
    return;
  }
  if (verb === "status") {
    if (!kind) {
      const status = await client.status({});
      console.log(formatStatus(status));
      if (status.verdict === "broken") process.exitCode = 1;
      return;
    }
    // One line per function: enabled, cadence, and its parked inputs (gold park_after).
    for (const s of await client.streamlines.list({}))
      if (!kind || s.source_key === kind)
        console.log([s.source_key, s.enabled ? "enabled" : "paused", s.cadence_tag ?? "on demand", `parked=${s.parked_inputs}`].join("\t"));
    return;
  }
  if (verb === "unpark") {
    console.log(JSON.stringify(await client.streamlines.unpark({ source_key: name.parse(kind) }), null, 2));
    return;
  }
  if (verb === "cadence") {
    console.log(
      JSON.stringify(
        await client.streamlines.requestCadenceChange({
          source_key: name.parse(kind),
          cadence: z.enum(["hourly", "daily", "weekly"]).parse(arg),
        }),
        null,
        2,
      ),
    );
    return;
  }
  throw new Error(
    "Usage: pnpm --dir control mdp new function <key> --class <class> --cadence <cadence> | new mart <name> | scaffold api <mart> | register | knobs set <source_key> --enable|--disable [--batch-size n] [--max-concurrency n] | cadence <key> <cadence> | tenants create --slug <slug> --name <name> | tenants list | tenants patch --tenant <slug> [--name <name>] [--status active|inactive] | keys create --tenant <slug> [--label <text>] [--expires <iso>] | keys create --role reader --global [--label <text>] [--expires <iso>] | keys list [--tenant <slug>] [--revoked] | keys revoke <id> | retry <cycle_id> | replay <cycle_id> | status [<key>] | unpark <key> | platform sources | platform holdings --since <UTC> | platform events [--after <cursor>] [--limit <count>]",
  );
}
if (process.argv[1] === fileURLToPath(import.meta.url))
  main(process.argv.slice(2)).catch((e) => {
    if (e instanceof LocalCommandError) {
      process.exitCode = 1;
      return;
    }
    if (e instanceof UsageError) {
      console.error(e.message);
      process.exitCode = 1;
      return;
    }
    console.error(e instanceof Error ? e.message : "Command failed");
    // control-api refuses a key before routing (a reader key, say) with only an HTTP status.
    const refusedStatus: Record<number, string> = { 401: "unauthorized", 403: "forbidden" };
    const code = e && typeof e === "object" ? e.data?.error_class ?? e.error_class ?? refusedStatus[e.status] : undefined;
    console.error(code ? errorHint(String(code)).next_step : "Check MDP_CONTROL_API_URL and the control API /health endpoint; start a local stack with bash ops/local/up.sh, then retry.");
    process.exitCode = 1;
  });
