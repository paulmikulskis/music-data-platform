import { createHash, randomBytes } from "node:crypto";
import { z } from "zod";
import { apiKeyDto, tenantDto } from "@mdp/contracts";
import { rows, one, AppError } from "./db.js";
import { impl } from "./router.shared.js";

const apiKeySelect =
  "SELECT k.id,k.label,k.tenant_id,k.role,k.warehouse_role,k.created_at,k.expires_at,k.revoked_at,t.slug AS tenant_slug FROM control.api_key k LEFT JOIN control.tenant t ON t.id=k.tenant_id";

export const apiKeysRouter = {
    list: impl.apiKeys.list.handler(({ context, input }) =>
      rows(
        context.db,
        apiKeyDto,
        `${apiKeySelect} WHERE ($1::text IS NULL OR t.slug=$1) AND ($2 OR k.revoked_at IS NULL) ORDER BY k.created_at DESC,k.id LIMIT 1000`,
        [input.tenant_slug ?? null, input.include_revoked],
      ),
    ),
    // Data-API keys: only the sha256 is stored; the key is returned once.
    // A global reader key (--global) has no tenant and reads global marts only.
    create: impl.apiKeys.create.handler(async ({ context, input }) => {
      const tenant = input.tenant_slug ? await one(context.db, tenantDto, "SELECT * FROM control.tenant WHERE slug=$1", [input.tenant_slug]) : null;
      if (tenant && tenant.status !== "active")
        throw new AppError("tenant_inactive", "Activate the tenant before issuing keys", 409);
      const key = `mdp_${randomBytes(32).toString("base64url")}`;
      const created = await one(
        context.db,
        z.object({ id: z.uuid() }),
        "INSERT INTO control.api_key(key_hash,label,tenant_id,role,expires_at,warehouse_role) VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
        [createHash("sha256").update(key).digest("hex"), input.label, tenant?.id ?? null, input.role, input.expires_at ?? null, input.warehouse_role ?? null],
      );
      const issued = await one(context.db, apiKeyDto, `${apiKeySelect} WHERE k.id=$1`, [created.id]);
      return { ...issued, api_key: key };
    }),
    // Keys are checked on every data request, so revocation applies to the next one.
    revoke: impl.apiKeys.revoke.handler(async ({ context, input }) => {
      await one(
        context.db,
        z.object({ id: z.uuid() }),
        "UPDATE control.api_key SET revoked_at=coalesce(revoked_at,now()) WHERE id=$1 RETURNING id",
        [input.id],
      );
      return one(context.db, apiKeyDto, `${apiKeySelect} WHERE k.id=$1`, [input.id]);
    }),
  };
