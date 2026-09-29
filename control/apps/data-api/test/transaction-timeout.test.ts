import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { expect, it } from "vitest";

const fixture = "apps/data-api/test/fixtures/transaction-timeout.ts";
it.skipIf(!process.env.MDP_WAREHOUSE_TEST_URL || !process.env.MDP_READER_URL)(
  "PostgreSQL ends active and idle reads, releases locks and replaces sessions; late errors stay caught", async () => {
    const { stdout, stderr } = await promisify(execFile)(process.execPath,
      ["--unhandled-rejections=strict", "--import", "tsx", fixture], { timeout: 35000 });
    const result = JSON.parse(stdout.trim());
    expect(result.status).toBe("passed");
    expect(result.results).toHaveLength(3);
    expect(stderr).toBe("");
    console.log(stdout.trim());
  }, 40000,
);

it("logs unexpected detached rejections with a correlation id and keeps the process alive", async () => {
  const { stdout, stderr } = await promisify(execFile)(process.execPath,
    ["--unhandled-rejections=throw", "--import", "tsx", fixture, "backstop"], { timeout: 5000 });
  expect(stdout).toContain("PASS process remains alive");
  expect(JSON.parse(stderr.trim())).toMatchObject({ event: "data_unhandled_rejection",
    correlation_id: "fixture-correlation", error_class: "data_unavailable", next_step: expect.any(String) });
  expect(stderr).not.toContain("private fixture body");
});
