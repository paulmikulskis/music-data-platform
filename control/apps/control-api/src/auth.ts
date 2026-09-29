import { createHash } from "node:crypto";
import { createClerkClient } from "@clerk/backend";
import { z } from "zod";
import { database, one, AppError, type DB } from "./db.js";
export type Identity = {
  actor: string;
  tenant_id: string | null;
  tenant_slug: string | null;
  admin: boolean;
  staff?: boolean;
  warehouse_role?: string | null;
  // The mdp-functions promoters' key: only PROMOTER_PROCEDURES, never a data read.
  promoter?: boolean;
};
// The target commands mdp_functions.promoter.ControlTargets calls.
export const PROMOTER_PROCEDURES = new Set([
  "streamlines.canaries",
  "targets.parkStale",
  "targets.listSets",
  "targets.lookup",
  "targets.createSet",
  "targets.importTargets",
  "targets.resolve",
  "targets.setSpec",
  "targets.bulkActivate",
]);
// `db` reads control.api_key and control.tenant: control_rt here, api_key_reader in the data API.
export async function authenticate(request: Request, db?: DB): Promise<Identity> {
  const key = request.headers.get("x-api-key");
  if (key) {
    const found = await one(
      db ?? database(),
      z.object({
        id: z.uuid(),
        tenant_id: z.uuid().nullable(),
        tenant_slug: z.string().nullable(),
        tenant_status: z.string().nullable(),
        role: z.string(),
        warehouse_role: z.string().nullable(),
      }),
      `SELECT k.id,k.tenant_id,t.slug AS tenant_slug,t.status AS tenant_status,k.role,k.warehouse_role FROM control.api_key k LEFT JOIN control.tenant t ON t.id=k.tenant_id WHERE key_hash=$1 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())`,
      [createHash("sha256").update(key).digest("hex")],
    ).catch((error: unknown) => {
      if (error instanceof AppError && error.error_class === "not_found")
        throw new AppError("unauthorized", "Invalid API key", 401);
      throw error;
    });
    if (found.tenant_id && found.tenant_status !== "active")
      throw new AppError("forbidden", "Tenant is inactive", 403);
    return {
      actor: `api-key:${found.id}`,
      tenant_id: found.tenant_id,
      tenant_slug: found.tenant_slug,
      admin: found.role === "admin",
      staff: found.role === "staff",
      warehouse_role: found.warehouse_role,
      promoter: found.role === "promoter",
    };
  }
  if (process.env.CLERK_SECRET_KEY) {
    const clerk = createClerkClient({
      secretKey: process.env.CLERK_SECRET_KEY,
    });
    const result = await clerk.authenticateRequest(request, {
      authorizedParties: (process.env.MDP_AUTHORIZED_PARTIES ?? "")
        .split(",")
        .filter(Boolean),
    });
    const auth = result.toAuth();
    if (!auth?.userId)
      throw new AppError("unauthorized", "Sign in with Clerk", 401);
    const user = await clerk.users.getUser(auth.userId);
    const meta = z
      .object({
        mdp_tenant_id: z.uuid().optional(),
        mdp_admin: z.boolean().optional(),
        mdp_staff: z.boolean().optional(),
        mdp_warehouse_role: z.string().regex(/^analyst_[a-z][a-z0-9_]{0,47}$/).optional(),
      })
      .parse(user.publicMetadata);
    const tenant = meta.mdp_tenant_id
      ? await one(
          db ?? database(),
          z.object({ slug: z.string() }),
          "SELECT slug FROM control.tenant WHERE id=$1 AND status='active'",
          [meta.mdp_tenant_id],
        ).catch((error: unknown) => {
          if (error instanceof AppError && error.error_class === "not_found")
            throw new AppError("forbidden", "Tenant is inactive", 403);
          throw error;
        })
      : null;
    return {
      actor: auth.userId,
      tenant_id: meta.mdp_tenant_id ?? null,
      tenant_slug: tenant?.slug ?? null,
      admin: meta.mdp_admin === true,
      staff: meta.mdp_admin !== true && meta.mdp_staff === true,
      warehouse_role: meta.mdp_warehouse_role ?? null,
    };
  }
  if (process.env.MDP_AUTH_MODE === "dev") {
    const supplied = request.headers.get("x-mdp-dev-user");
    if (supplied && supplied !== "dev-user")
      throw new AppError("unauthorized", "Dev identity must be dev-user", 401);
    return {
      actor: "dev-user",
      tenant_id: process.env.MDP_DEV_TENANT_ID ?? null,
      tenant_slug: process.env.MDP_DEV_TENANT_SLUG ?? null,
      admin: true,
    };
  }
  throw new AppError("unauthorized", "Authentication is not configured", 401);
}
export function checkOrigin(request: Request) {
  const origin = request.headers.get("origin");
  const trusted = (process.env.MDP_TRUSTED_BROWSER_ORIGINS ?? "").split(",").map(value => value.trim()).filter(value => {
    try { return new URL(value).origin === value && ["https:", "http:"].includes(new URL(value).protocol); }
    catch { return false; }
  });
  if (origin && origin !== new URL(request.url).origin && !trusted.includes(origin))
    throw new AppError("forbidden", "Cross-origin mutation refused", 403);
}

// Staff analysis is explicit because these POSTs do not change operator configuration.
export const STAFF_ANALYSIS_PATHS = new Set([
  "sandboxStatus", "queries", "createSession", "history", "draft", "query",
  "previewModel", "backtest", "explain", "lineage", "saveAsPr", "status",
  "cancel", "result", "artifactContent", "artifact", "models",
].map(action => `/workbench/${action}`));
