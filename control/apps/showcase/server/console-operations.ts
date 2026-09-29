import { randomUUID } from "node:crypto";
export type ConsolePayload = {
  status: number;
  headers: [string, string][];
  body: ArrayBuffer;
};
type Operation = {
  session: string;
  at: number;
  result?: ConsolePayload;
  failed?: boolean;
  promise: Promise<ConsolePayload>;
};
export class ConsoleOperations {
  private operations = new Map<string, Operation>();
  start(session: string, work: () => Promise<ConsolePayload>) {
    for (const [key, value] of this.operations)
      if ((value.result || value.failed) && value.at < Date.now() - 600000)
        this.operations.delete(key);
    if (this.operations.size >= 256) {
      const finished = [...this.operations].find(
        ([, value]) => value.result || value.failed,
      );
      if (finished) this.operations.delete(finished[0]);
    }
    if (this.operations.size >= 256)
      throw new Error("Operation list is full. Open /status.");
    const id = randomUUID();
    const operation: Operation = { session, at: Date.now(), promise: work() };
    operation.promise.then(
      (result) => {
        operation.result = result;
        operation.at = Date.now();
      },
      () => {
        operation.failed = true;
        operation.at = Date.now();
      },
    );
    this.operations.set(id, operation);
    return { id, promise: operation.promise };
  }
  get(id: string, session: string) {
    const value = this.operations.get(id);
    return value?.session === session ? value : undefined;
  }
}
export const consoleOperations = (globalThis.showcaseConsoleOperations ??=
  new ConsoleOperations());
export function operationPending(id: string) {
  const href = `/s/operations/${id}`;
  return new Response(
    `<!doctype html><title>Work continues</title><p>The request is still running.</p><a href="${href}">Check this request</a>`,
    {
      status: 202,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        Location: href,
      },
    },
  );
}

declare global {
  var showcaseConsoleOperations: ConsoleOperations | undefined;
}
