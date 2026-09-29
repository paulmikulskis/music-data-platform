import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { id, get, post } from "./shared.js";
import { tenantSlug } from "./tenants.js";

// Key metadata only; the key itself appears once, in the create response.
export const apiKeyDto = s.api_key
  .pick({ id: true, label: true, tenant_id: true, role: true, warehouse_role: true })
  .extend({
    tenant_slug: z.string().nullable(),
    created_at: z.iso.datetime(),
    expires_at: z.iso.datetime().nullable(),
    revoked_at: z.iso.datetime().nullable(),
  });

const readerChoice =
  "Run pnpm --dir control mdp keys create --role reader --global for global marts, or keys create --tenant <slug> for one tenant's marts.";

// A reader key reads through the data API: a tenant key reads its tenant's marts, a global key
// (no tenant) reads global marts. Control-api refuses every request from a reader key.
export const apiKeyCreateInput = z
  .object({
    tenant_slug: tenantSlug.optional(),
    global: z.boolean().default(false),
    role: z.enum(["reader", "staff"]).default("reader"),
    warehouse_role: z.string().regex(/^analyst_[a-z][a-z0-9_]{0,47}$/).optional(),
    label: z.string().min(1).max(200),
    expires_at: z.iso.datetime({ offset: true }).optional(),
  })
  .superRefine((value, context) => {
    const refuse = (message: string) => context.addIssue({ code: "custom", message });
    if (value.role === "staff" && (value.tenant_slug || value.global))
      refuse("Staff keys take no --tenant or --global. Run pnpm --dir control mdp keys create --role staff --label <text>.");
    if (value.role === "reader" && value.tenant_slug && value.global)
      refuse(`Use --tenant or --global, not both. ${readerChoice}`);
    if (value.role === "reader" && !value.tenant_slug && !value.global)
      refuse(`A reader key needs --tenant <slug> or --global. ${readerChoice}`);
    if (value.role === "reader" && value.warehouse_role)
      refuse("Only staff keys take --warehouse-role. Run pnpm --dir control mdp keys create --role staff --label <text> --warehouse-role analyst_<handle>.");
  });

export const apiKeysContract = {
    list: get(
      "/api-keys",
      z.object({ tenant_slug: tenantSlug.optional(), include_revoked: z.boolean().default(false) }),
      z.array(apiKeyDto),
    ),
    create: post("/api-keys/create", apiKeyCreateInput, apiKeyDto.extend({ api_key: z.string() })),
    revoke: post("/api-keys/revoke", id, apiKeyDto),
  };
