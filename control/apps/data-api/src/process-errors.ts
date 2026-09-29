import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";

export const requestCorrelation = new AsyncLocalStorage<string>();

export function logReadFailure(event: string) {
  // Driver errors can contain SQL, credentials and row values. Log only our fixed event.
  console.error(JSON.stringify({ event, correlation_id: requestCorrelation.getStore() ?? randomUUID(),
    error_class: "data_unavailable", next_step: "Inspect the data API log using this correlation id; retry the read" }));
}

// Last-resort visibility for detached promises. Expected failures are handled at their caller.
export function installRejectionHandler() {
  const handler = () => logReadFailure("data_unhandled_rejection");
  process.on("unhandledRejection", handler);
  return () => process.off("unhandledRejection", handler);
}
