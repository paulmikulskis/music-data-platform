import { z } from "zod";
import { selectSchemas as s } from "@mdp/control-db";
import { id, empty, get, post } from "./shared.js";

export const tenantDto = s.tenant.pick({
  id: true,
  slug: true,
  name: true,
  status: true,
});

export const tenantSlug = z.string().regex(/^[a-z][a-z0-9-]*$/);

export const tenantsContract = {
    list: get("/tenants", empty, z.array(tenantDto)),
    create: post(
      "/tenants/create",
      z.object({
        slug: z.string().regex(/^[a-z][a-z0-9-]*$/),
        name: z.string().min(1),
      }),
      tenantDto,
    ),
    patch: post(
      "/tenants/patch",
      id.extend({
        name: z.string().optional(),
        status: z.enum(["active", "inactive"]).optional(),
      }),
      tenantDto,
    ),
  };
