// Target set members: the one target identity rule and the importer's update path.
//
// Identity. A row names a member of its set by platform and either its platform id or its handle.
// A row with an id matches the member holding that id; a row without one matches the member with its
// folded handle (lower case, no leading @), and a row whose id no member holds yet matches the id-less
// member with its folded handle, which then gains the id in place. `matchMember` is the rule; the tenant
// importer, keyed commands all resolve a member through it, so an
// account stays one target from its handle to its id.
//
// Update path. `writeMembers(db, setId, rows, dryRun, options)` is generic over resource_kind and spec
// shape. Each row is an add (a new pending target and its spec), an update in place (a changed spec,
// display name, handle or gained id), or unchanged (an equal spec is skipped). The dry run reports the
// same plan and writes nothing. Each result row carries its `op`; an update also carries `before` and
// `after` (and `key_transition` when its canonical_key moves), so undo can restore it. An update keeps the
// member's values for `options.keep` params (the scope's act key and role) and, with `keepRole`, its
// target role. `undoMembers` reverses one import from its audited result rows.
//
// The audit redacts a params field named `key` or matching its secret pattern (audit.ts `sanitize`), so a
// spec carrying one cannot be undone and `undoMembers` refuses it.
import { z } from "zod";
import { jsonRow, targetDto } from "@mdp/contracts";
import { AppError, one, rows, type DB } from "./db.js";

export const foldHandle = (handle: string) => handle.trim().replace(/^@/, "").toLowerCase();
export type IdentityRow = { platform: string; platform_account_id: string | null; handle: string | null };
/** The row's identity under the rule: `<platform>:<id>`, else `<platform>:@<folded handle>`. */
export function identityKey(row: IdentityRow) {
  if (row.platform_account_id) return `${row.platform}:${row.platform_account_id}`;
  if (row.handle) return `${row.platform}:@${foldHandle(row.handle)}`;
  throw new AppError("invalid_csv", "A member needs its platform id or its handle", 422);
}
/** A handle-only member's canonical key, `<prefix>:@<folded handle>` (for example `tt:account:@act.alpha`). */
export const handleKey = (prefix: string, handle: string) => `${prefix}:@${foldHandle(handle)}`;
const HANDLE_FORM = /:@[^:]*$/;
/** The id form of a handle-form canonical key; any other key is already in its id form. */
export const idFormKey = (key: string, id: string) => {
  const at = key.search(HANDLE_FORM);
  return at < 0 ? key : `${key.slice(0, at)}:${id}`;
};

export const memberDto = targetDto.extend({
  resource_kind: z.string().nullable(),
  canonical_key: z.string().nullable(),
  params_json: jsonRow.nullable(),
});
export type Member = z.output<typeof memberDto>;
const memberSelect = `SELECT t.*,s.resource_kind,s.canonical_key,s.params_json
  FROM control.target t LEFT JOIN control.target_spec s ON s.target_id=t.id`;
// Where two members share an identity (pending duplicates an earlier importer left), a resolved one
// wins, then the oldest.
const preferred = "ORDER BY (t.resolution_status='resolved') DESC,t.created_at,t.id";
/** Every member of a set in preference order; `lock` holds their rows for the caller's transaction. */
export const loadMembers = (db: DB, setId: string, lock = false) =>
  rows(db, memberDto, `${memberSelect} WHERE t.target_set_id=$1 ${preferred}${lock ? " FOR UPDATE OF t" : ""}`, [setId]);

/** The one target identity rule over `members` (in `loadMembers` order). */
export function matchMember(members: readonly Member[], row: IdentityRow): Member | undefined {
  const same = members.filter((m) => m.platform === row.platform);
  if (row.platform_account_id) {
    const held = same.find((m) => m.platform_account_id === row.platform_account_id);
    if (held || !row.handle) return held;
  }
  if (!row.handle) return undefined;
  const handle = foldHandle(row.handle);
  return same.find((m) => m.handle !== null && foldHandle(m.handle) === handle
    && (!row.platform_account_id || m.platform_account_id === null));
}
/** One row's member in a set, read by key (the keyed commands' lookup). */
export async function findMember(db: DB, setId: string, row: IdentityRow) {
  const found = await rows(db, memberDto,
    `${memberSelect} WHERE t.target_set_id=$1 AND t.platform=$2
       AND (t.platform_account_id=$3 OR lower(regexp_replace(btrim(t.handle),'^@',''))=$4) ${preferred}`,
    [setId, row.platform, row.platform_account_id, row.handle ? foldHandle(row.handle) : null]);
  return matchMember(found, row);
}

export type MemberRow = IdentityRow & {
  display_name: string | null;
  role: string | null;
  resource_kind: string;
  canonical_key: string;
  params_json: Record<string, unknown>;
};
export type MemberOptions = {
  /** Spec params whose stored value an update keeps. */
  keep?: readonly string[];
  /** Whether an update keeps the member's target role. */
  keepRole?: boolean;
  /** Rewrites the rows once the set's members are loaded (and locked, outside a dry run). */
  prepare?: (members: Member[], rows: MemberRow[]) => Promise<MemberRow[]> | MemberRow[];
};
const fields = z.object({
  platform_account_id: z.string().nullable(), handle: z.string().nullable(), display_name: z.string().nullable(),
  role: z.string().nullable(), resource_kind: z.string().nullable(), canonical_key: z.string().nullable(),
  params_json: jsonRow.nullable(),
});
type Fields = z.output<typeof fields>;
const json = (value: unknown) => jsonRow.parse(JSON.parse(JSON.stringify(value)));
const fieldsOf = (m: Member): Fields => ({
  platform_account_id: m.platform_account_id, handle: m.handle, display_name: m.display_name, role: m.role,
  resource_kind: m.resource_kind, canonical_key: m.canonical_key, params_json: m.params_json,
});
const rowFields = (row: MemberRow): Fields => ({
  platform_account_id: row.platform_account_id, handle: row.handle, display_name: row.display_name, role: row.role,
  resource_kind: row.resource_kind, canonical_key: row.canonical_key, params_json: json(row.params_json),
});
// Key order never makes two specs differ; list order does (the importer sorts its lists).
const stable = (value: unknown) => JSON.stringify(value, (_key, item: unknown) =>
  item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : item);

function desired(row: MemberRow, member: Member, options: MemberOptions): Fields {
  const id = row.platform_account_id ?? member.platform_account_id;
  const stored = member.params_json ?? {};
  const kept = Object.fromEntries((options.keep ?? []).filter((k) => k in stored).map((k) => [k, stored[k]]));
  return {
    platform_account_id: id,
    handle: row.handle ?? member.handle,
    display_name: row.display_name ?? member.display_name,
    role: options.keepRole && member.role !== null ? member.role : row.role,
    resource_kind: row.resource_kind,
    // A handle-only row for a member that already has its id keeps the id form.
    canonical_key: id && !row.platform_account_id ? idFormKey(row.canonical_key, id) : row.canonical_key,
    params_json: json({ ...row.params_json, ...kept }),
  };
}
export type MemberPlan = { row: MemberRow; after: Fields }
  & ({ op: "add"; member?: undefined } | { op: "update" | "unchanged"; member: Member });
/** Each row's op under the identity rule; two rows naming one target are refused. */
export function planMembers(members: readonly Member[], input: readonly MemberRow[], options: MemberOptions = {}): MemberPlan[] {
  const claimed = new Set<string>();
  return input.map((row, index) => {
    const member = matchMember(members, row);
    const claims = member ? [member.id]
      : [identityKey(row), ...(row.handle ? [identityKey({ ...row, platform_account_id: null })] : [])];
    if (claims.some((c) => claimed.has(c)))
      throw new AppError("invalid_csv", `Line ${index + 2}: another row of this import names the same target.`, 422);
    claims.forEach((c) => claimed.add(c));
    if (!member) return { op: "add", row, after: rowFields(row) };
    const after = desired(row, member, options);
    return { op: stable(after) === stable(fieldsOf(member)) ? "unchanged" : "update", row, member, after };
  });
}
function result(op: MemberPlan["op"], target: Record<string, unknown>, after: Fields, before?: Fields) {
  const row: Record<string, unknown> = {
    op, ...target, resource_kind: after.resource_kind, canonical_key: after.canonical_key, params_json: after.params_json,
  };
  if (before) {
    row.before = before;
    row.after = after;
    if (before.canonical_key !== after.canonical_key) row.key_transition = { from: before.canonical_key, to: after.canonical_key };
  }
  return json(row);
}
const upsertSpec = `INSERT INTO control.target_spec(target_id,resource_kind,canonical_key,params_json) VALUES ($1,$2,$3,$4::text::jsonb)
  ON CONFLICT(target_id) DO UPDATE SET resource_kind=EXCLUDED.resource_kind,canonical_key=EXCLUDED.canonical_key,params_json=EXCLUDED.params_json`;
const setTarget = "UPDATE control.target SET platform_account_id=$2,handle=$3,display_name=$4,role=$5,updated_at=now() WHERE id=$1 RETURNING *";
const pickTarget = (f: Fields) => ({ platform_account_id: f.platform_account_id, handle: f.handle, display_name: f.display_name, role: f.role });

/** The importer's update path: plan every row against the set's members, then (outside a dry run) write. */
export async function writeMembers(db: DB, setId: string, input: MemberRow[], dryRun: boolean, options: MemberOptions = {}) {
  // Imports into one set take turns, so two of them never add the same account twice.
  if (!dryRun) await db.unsafe("SELECT pg_advisory_xact_lock(hashtextextended('control.target_set:' || $1::text, 0))", [setId]);
  const members = await loadMembers(db, setId, !dryRun);
  const plans = planMembers(members, options.prepare ? await options.prepare(members, input) : input, options);
  const out = [];
  for (const plan of plans) {
    const { after } = plan;
    const before = plan.op === "update" ? fieldsOf(plan.member) : undefined;
    if (dryRun || plan.op === "unchanged") {
      const target = plan.member ? { ...targetDto.parse(plan.member), ...pickTarget(after) }
        : { ...pickTarget(after), platform: plan.row.platform, resolution_status: "pending" };
      out.push(result(plan.op, target, after, before));
      continue;
    }
    const target = plan.op === "add"
      ? await one(db, targetDto,
        "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,display_name,role,resolution_status) VALUES ($1,$2,$3,$4,$5,$6,'pending') RETURNING *",
        [setId, plan.row.platform, after.platform_account_id, after.handle, after.display_name, after.role])
      : await one(db, targetDto, setTarget, [plan.member.id, after.platform_account_id, after.handle, after.display_name, after.role]);
    await db.unsafe(upsertSpec, [target.id, after.resource_kind, after.canonical_key, JSON.stringify(after.params_json)]);
    out.push(result(plan.op, target, after, before));
  }
  const tally = (op: MemberPlan["op"]) => plans.filter((p) => p.op === op).length;
  return { dry_run: dryRun, count: plans.length, adds: tally("add"), updates: tally("update"), unchanged: tally("unchanged"), rows: out };
}
const audited = z.object({ id: z.uuid(), op: z.enum(["add", "update", "unchanged"]).optional(), before: fields.optional(), after: fields.optional() });
/**
 * Reverses one import from its audited result rows: deletes its adds (still pending and never activated)
 * and restores each updated member's target fields and spec from `before`, refusing when anything changed
 * the member since. Rows without an `op` (an insert-only import) are adds. Returns the members reversed.
 */
export async function undoMembers(db: DB, auditedRows: unknown[]) {
  const parsed = z.array(audited).parse(auditedRows);
  const adds = parsed.filter((r) => (r.op ?? "add") === "add");
  const updates = parsed.filter((r) => r.op === "update");
  const current = await rows(db, memberDto, `${memberSelect} WHERE t.id=ANY($1::uuid[]) FOR UPDATE OF t`,
    [[...adds, ...updates].map((r) => r.id)]);
  const byId = new Map(current.map((m) => [m.id, m]));
  const usedAdd = adds.some((r) => {
    const m = byId.get(r.id);
    return !m || m.resolution_status !== "pending" || m.activated_at;
  });
  const usedUpdate = updates.some((r) => {
    const m = byId.get(r.id);
    return !m || !r.before || !r.after || stable(fieldsOf(m)) !== stable(r.after)
      || (r.before.platform_account_id === null && m.resolution_status === "resolved")
      || stable(r.before).includes('"[REDACTED]"');
  });
  if (usedAdd || usedUpdate)
    throw new AppError("import_already_used",
      "Only an import whose added targets are still pending and never activated, and whose updated targets are unchanged since, can be undone", 409);
  const ids = adds.map((r) => r.id);
  await db.unsafe("DELETE FROM control.target_spec WHERE target_id=ANY($1::uuid[])", [ids]);
  await db.unsafe("DELETE FROM control.target WHERE id=ANY($1::uuid[])", [ids]);
  for (const r of updates) {
    const before = r.before;
    if (!before) continue;
    await db.unsafe(setTarget, [r.id, before.platform_account_id, before.handle, before.display_name, before.role]);
    if (before.resource_kind === null || before.canonical_key === null)
      await db.unsafe("DELETE FROM control.target_spec WHERE target_id=$1", [r.id]);
    else await db.unsafe(upsertSpec, [r.id, before.resource_kind, before.canonical_key, JSON.stringify(before.params_json ?? {})]);
  }
  return adds.length + updates.length;
}

/**
 * `targets.resolve`: a handle-form canonical_key moves to its id form when the target gets its id.
 * Returns the key transition, or undefined when the key already had its id form (or no spec exists).
 */
export async function resolveKey(db: DB, targetId: string, id: string) {
  const [spec] = await rows(db, z.object({ canonical_key: z.string() }),
    "SELECT canonical_key FROM control.target_spec WHERE target_id=$1 FOR UPDATE", [targetId]);
  if (!spec || idFormKey(spec.canonical_key, id) === spec.canonical_key) return undefined;
  const to = idFormKey(spec.canonical_key, id);
  await db.unsafe("UPDATE control.target_spec SET canonical_key=$2 WHERE target_id=$1", [targetId, to]);
  return { from: spec.canonical_key, to };
}

/** The modified time (epoch ms) of a source ref, `<repo>:<path>@<commit time>`. */
export const sourceTime = (ref: string) => Date.parse(ref.slice(ref.lastIndexOf("@") + 1));
/**
 * `targets.importTargets --source-ref`: the audit row records the ref, and one older than the
 * newest ref a succeeded import into the set recorded is refused with `stale_export`.
 */
export async function refuseStaleSource(db: DB, setId: string, ref: string | undefined) {
  if (!ref) return;
  const recorded = await rows(db, z.object({ source_ref: z.string() }),
    `SELECT a.after->'input'->>'source_ref' AS source_ref FROM control.audit_log a
     WHERE a.action='targets.importTargets' AND a.subject=$1 AND a.after->>'state'='succeeded'
       AND a.after->'result'->>'dry_run'='false' AND a.after->'input'->>'source_ref' IS NOT NULL`, [setId]);
  const newest = Math.max(-Infinity, ...recorded.map((r) => sourceTime(r.source_ref)).filter(Number.isFinite));
  if (sourceTime(ref) < newest)
    throw new AppError("stale_export",
      `This export's modified time is older than the set's last import (${new Date(newest).toISOString()}); import the newer export.`, 409);
}
