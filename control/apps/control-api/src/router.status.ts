import { platformStatus } from "./status.js";
import { impl } from "./router.shared.js";

export const statusRouter = impl.status.handler(({ context }) => platformStatus(context.db));
