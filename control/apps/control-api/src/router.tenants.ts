import { z } from "zod";
import { tenantDto } from "@mdp/contracts";
import { rows, one, AppError, type DB } from "./db.js";
import { impl, dbOf, patch } from "./router.shared.js";

export const tenantsRouter = {
    list: impl.tenants.list.handler(({ context }) =>
      rows(
        dbOf(context),
        tenantDto,
        "SELECT * FROM control.tenant ORDER BY slug",
      ),
    ),
    create: impl.tenants.create.handler(async ({ context, input }) => {
      const [tenant] = await rows(
        context.db,
        tenantDto,
        "INSERT INTO control.tenant(slug,name) VALUES ($1,$2) ON CONFLICT (slug) DO NOTHING RETURNING *",
        [input.slug, input.name],
      );
      if (!tenant) throw new AppError("tenant_exists", `Tenant ${input.slug} already exists.`, 409);
      return tenant;
    }),
    patch: impl.tenants.patch.handler(async ({ context, input }) => {
      try {
        return await patch(context.db, tenantDto, "tenant", "id", input.id, {
          name: input.name,
          status: input.status,
        });
      } catch (e) {
        if (e instanceof AppError && e.error_class === "not_found")
          throw new AppError("not_found", "Tenant does not exist.", 404);
        throw e;
      }
    }),
  };
