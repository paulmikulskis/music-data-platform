import type { ContractRouterClient } from '@orpc/contract';
import type { contract } from '@mdp/contracts';
import { errorHint } from '@mdp/contracts';

type ReadEvents = ContractRouterClient<typeof contract>['platform']['events'];

export class PlatformEventsCapacityError extends Error {
  readonly error_class = 'platform_events_capacity';
  readonly next_step = errorHint(this.error_class).next_step;
  constructor() { super('The event reader needs more space for recent keys. Open /ops/platform to inspect the overlap.'); }
}

/** Keep one reader per poller and await each page before reading the next one. */
export function createPlatformEventReader(read: ReadEvents, initialCursor?: string) {
  let recent = new Map<string, number>();
  let after: string | undefined = initialCursor;
  return async (limit = 100) => {
    const page = await read({ after, limit });
    // Check duplicates before pruning: this page can span the old and new overlap bounds.
    const events = page.events.filter(event => !recent.has(event.key));
    const lower = page.overlap_start === null ? -Infinity : Date.parse(page.overlap_start);
    // Millisecond rounding retains boundary keys a little longer; it never expires them early.
    const retained = new Map([...recent].filter(([, time]) => time >= lower));
    for (const event of page.events) {
      const time = Date.parse(event.occurred_at);
      if (time >= lower) retained.set(event.key, Math.max(time, retained.get(event.key) ?? -Infinity));
    }
    // Commit cursor and keys together. A refusal leaves both unchanged for the next attempt.
    if (retained.size > 10000) throw new PlatformEventsCapacityError();
    recent = retained;
    after = page.next_cursor;
    return { ...page, events };
  };
}
