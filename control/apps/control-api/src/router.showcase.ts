import { prepareLink, recordActor } from "@mdp/showcase-auth";
import { AppError } from "./db.js";
import { impl } from "./router.shared.js";

export const showcaseRouter = {
  link: impl.showcase.link.handler(async ({ context, input }) => {
    if (!context.identity.admin) {
      throw new AppError(
        "forbidden",
        "This action needs the admin role. Ask the platform operator to mint the link.",
        403,
      );
    }
    const prepared = (() => {
      try {
        return prepareLink(input.person, input.ttl_seconds);
      } catch {
        throw new AppError(
          "showcase_link_unavailable",
          "The sign-in link could not be created. Open docs/operating.md#give-a-viewer-access to check the setup.",
          503,
        );
      }
    })();
    const { p, link, url } = prepared;
    await context.db.unsafe(
      "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
      [`showcase:${link.handle}`],
    );
    await recordActor(context.db, p);
    await context.db.unsafe(
      "INSERT INTO control.showcase_link (nonce, handle, expires_at, created_by) VALUES ($1, $2, to_timestamp($3), $4)",
      [link.nonce, link.handle, link.exp, context.identity.actor],
    );
    return { link_url: url };
  }),
};
