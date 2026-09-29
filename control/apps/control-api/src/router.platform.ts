import { AppError, platformRead } from "./db.js";
import { impl } from "./router.shared.js";
import {
  readHoldings,
  readRunnerState,
  readSources,
  readNight,
  readRelationCounts,
} from "./platform.js";
import { readPlatformEvents } from "./platform-events.js";

export const platformRouter = {
  night: impl.platform.night.handler(async ({ context, input }) => {
    if (!context.identity.admin)
      throw new AppError(
        "forbidden",
        "Night readings need the admin role. Open the access runbook.",
        403,
      );
    return platformRead(context.db, (tx) => readNight(tx, input, context.db));
  }),
  relationCounts: impl.platform.relationCounts.handler(
    async ({ context, input }) => {
      if (!context.identity.admin)
        throw new AppError(
          "forbidden",
          "Stored counts need the admin role. Open the access runbook.",
          403,
        );
      return platformRead(context.db, (tx) => readRelationCounts(tx, input));
    },
  ),
  sources: impl.platform.sources.handler(async ({ context }) => {
    if (!context.identity.admin)
      throw new AppError(
        "forbidden",
        "Sources needs the admin role. Open the access runbook.",
        403,
      );
    return platformRead(context.db, (tx) => readSources(tx));
  }),
  holdings: impl.platform.holdings.handler(async ({ context, input }) => {
    if (!context.identity.admin)
      throw new AppError(
        "forbidden",
        "Holdings needs the admin role. Open the access runbook.",
        403,
      );
    return platformRead(context.db, (tx) => readHoldings(tx, input.since));
  }),
  events: impl.platform.events.handler(async ({ context, input }) => {
    if (!context.identity.admin)
      throw new AppError(
        "forbidden",
        "Events needs the admin role. Open the access runbook.",
        403,
      );
    return platformRead(context.db, async (tx) => {
      const [page, runner] = await Promise.all([
        readPlatformEvents(tx, input),
        readRunnerState(tx),
      ]);
      return {
        ...page,
        runner,
        next_step:
          "Pass next_cursor as after to continue. Filter repeated event keys. Open /ops to inspect current work.",
      };
    });
  }),
};
