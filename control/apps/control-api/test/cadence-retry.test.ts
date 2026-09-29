import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { createRouterClient } from "@orpc/server";
import type postgres from "postgres";
import { isolatedControl } from "./isolated-control.js";
import * as database from "../src/db.js";
import { router } from "../src/router.js";
import { consoleFailures, retryOrNote } from "../src/console-data.js";
import { FailureBanner } from "../src/primitives.js";
import { launchCore } from "../src/core-launcher.js";

vi.mock("../src/core-launcher.js", () => ({
  launchCore: vi.fn(async (cadence: string, scope: string) => ({
    launcher: "record",
    machine_id: null,
    cadence,
    scope,
    command: [cadence],
    env: {},
  })),
}));

// Retry rebuilds the newest non-superseded scheduled cycle, so the banner offers it only while that
// cycle is open, and Retry refuses a superseded input instead of rebuilding a different cycle.
describe("Retry on cadence alerts", () => {
  it("offers Retry for an open current cycle and a note for a closed one", async () => {
    expect(
      retryOrNote({
        cycle_id: "old",
        current_cycle_id: "cur",
        current_status: "open",
      }),
    ).toEqual({ cycle_id: "cur" });
    expect(
      retryOrNote({
        cycle_id: "old",
        current_cycle_id: "bc30ad30-0000",
        current_status: "closed",
        current_closed_at: "2026-09-28T17:53:02.123Z",
      }),
    ).toEqual({
      note: "Cycle bc30ad30 closed 2026-09-28 17:53 UTC. The next successful build resolves this alert.",
    });
    expect(retryOrNote({ cycle_id: "old" })).toEqual({});
    const html = String(
      await FailureBanner({
        failures: [
          {
            title: "cadence failed",
            message: "Model failed.",
            note: "Cycle bc30ad30 closed.",
            runbook: "/runbooks/cadence-failed",
          },
        ],
      }),
    );
    expect(html).not.toContain("/actions/retry");
    expect(html).toContain("Cycle bc30ad30 closed.");
    expect(html).toContain("/runbooks/cadence-failed");
  });
});

const identity = {
  actor: "api-key:fixture-operator",
  admin: true,
  tenant_id: null,
  tenant_slug: null,
};
let db: postgres.Sql;
let close: (() => Promise<void>) | undefined;
const client = () => createRouterClient(router, { context: { identity, db } });

beforeAll(async () => {
  if (process.env.MDP_STATUS_TEST_URL)
    ({ db, close } = await isolatedControl(process.env.MDP_STATUS_TEST_URL));
}, 30000);
beforeEach(() => {
  if (db) vi.spyOn(database, "database").mockImplementation(() => db);
  vi.mocked(launchCore).mockClear();
});
afterAll(async () => close?.(), 30000);

describe.skipIf(!process.env.MDP_STATUS_TEST_URL)(
  "Retry on cadence alerts against control",
  () => {
    it("names the closed current cycle, refuses the superseded one, and offers Retry once a cycle is open", async () => {
      const [superseded] =
        await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at,status,manifest_mode)
      VALUES ('daily','global','core:first',now()-interval '15 hours','superseded','stamp') RETURNING id::text`;
      const [closed] =
        await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at,status,closed_at,close_no,manifest_mode)
      VALUES ('daily','global','core:second',now()-interval '14 hours','closed',now()-interval '1 hour',1,'stamp') RETURNING id::text`;
      for (const cycle of [superseded!.id, closed!.id])
        await db`INSERT INTO control.alert(class,severity,subject_type,subject_id) VALUES ('cadence_failed','critical','cycle',${cycle})`;
      let failures = (await consoleFailures(db)).filter(
        (f) => f.title === "cadence failed",
      );
      expect(failures).toHaveLength(2);
      for (const failure of failures) {
        expect(failure.cycle_id).toBeUndefined();
        expect(failure.note).toContain(
          `Cycle ${closed!.id.slice(0, 8)} closed`,
        );
      }
      const closedBanner = String(await FailureBanner({ failures }));
      expect(closedBanner).not.toContain("/actions/retry");
      expect(closedBanner.match(/class="failure-group"/g)).toHaveLength(1);
      expect(closedBanner).toContain("× 2");
      expect(closedBanner).toContain(`Cycle ${closed!.id.slice(0, 8)} closed`);
      expect(closedBanner).toContain("/runbooks/cadence-failed");

      await expect(
        client().dbt.retry({ cycle_id: superseded!.id }),
      ).rejects.toMatchObject({
        data: {
          error_class: "cycle_superseded",
          message: expect.stringContaining(
            `current cycle ${closed!.id} is closed`,
          ),
        },
      });
      expect(launchCore).not.toHaveBeenCalled();

      const [open] =
        await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,manifest_mode)
      VALUES ('daily','global','core:third','stamp') RETURNING id::text`;
      failures = (await consoleFailures(db)).filter(
        (f) => f.title === "cadence failed",
      );
      expect(failures.map((f) => f.cycle_id)).toEqual([open!.id, open!.id]);
      const openBanner = String(await FailureBanner({ failures }));
      expect(openBanner.match(/class="failure-group"/g)).toHaveLength(1);
      expect(openBanner).toContain("/actions/retry");
      expect(openBanner).toContain(`name="cycle_id" value="${open!.id}"`);
      expect(openBanner).not.toContain(`name="cycle_id" value="${closed!.id}"`);
      expect(openBanner).not.toContain(
        `name="cycle_id" value="${superseded!.id}"`,
      );
      await expect(
        client().dbt.retry({ cycle_id: superseded!.id }),
      ).rejects.toMatchObject({
        data: {
          message: expect.stringContaining(
            `Retry the current cycle ${open!.id} instead`,
          ),
        },
      });
      await client().dbt.retry({ cycle_id: closed!.id });
      expect(launchCore).toHaveBeenCalledTimes(1);
    });
  },
);
