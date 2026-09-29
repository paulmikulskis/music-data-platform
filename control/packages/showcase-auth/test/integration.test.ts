import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import postgres from "postgres";
import {
  mint,
  redeem,
  revoke,
  validateSession,
  hash,
  verifyLink,
  signLink,
  signOut,
} from "../src/index.js";
const url = process.env.MDP_SHOWCASE_TEST_URL;
const invented = {
  handle: "quartz",
  display_name: "Quartz",
  email: "quartz@example.invalid",
  admin_key: "invented-key",
  api_key_id: "00000000-0000-4000-8000-000000000001",
};
describe.skipIf(!url)("showcase on disposable Postgres", () => {
  let db: ReturnType<typeof postgres>, owner: ReturnType<typeof postgres>;
  beforeAll(() => {
    if (!url || !["127.0.0.1", "localhost"].includes(new URL(url).hostname))
      throw new Error("Use an isolated loopback database.");
    db = postgres(url, { max: 6, connection: { lock_timeout: 250 } });
    const admin = new URL(url);
    admin.username = "postgres";
    admin.password = "postgres";
    owner = postgres(admin.toString());
    vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
    vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  });
  beforeEach(async () => {
    vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", undefined);
    vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", undefined);
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([invented]));
    await owner`TRUNCATE control.showcase_link, control.showcase_session, control.showcase_actor`;
    await owner`DELETE FROM control.audit_log WHERE action LIKE 'showcase.%'`;
  });
  afterAll(async () => {
    await db?.end();
    await owner?.end();
    vi.unstubAllEnvs();
  });
  const token = async () =>
    new URL(await mint(db, "quartz")).searchParams.get("t")!;
  it("concurrent redemption creates one session with one sign-in audit row", async () => {
    const t = await token();
    const results = await Promise.all([
      redeem(db, t, "test"),
      redeem(db, t, "test"),
    ]);
    expect(results.filter(Boolean)).toHaveLength(1);
    const rows = await db`SELECT id_hash FROM control.showcase_session`;
    expect(rows).toHaveLength(1);
    expect(rows[0]!.id_hash).toBe(hash(results.find(Boolean)!));
    expect(
      await db`SELECT * FROM control.audit_log WHERE action='showcase.sign_in'`,
    ).toHaveLength(1);
  });
  it("signature checks used by previews leave the nonce untouched", async () => {
    const t = await token();
    expect(verifyLink(t)).not.toBeNull();
    expect(verifyLink(t)).not.toBeNull();
    expect(
      (await db`SELECT used_at FROM control.showcase_link`)[0]!.used_at,
    ).toBeNull();
    expect(await redeem(db, t, null)).toBeTruthy();
  });
  it("revokes unused links and existing sessions, including removed people", async () => {
    const t = await token(),
      id = await redeem(db, t, null),
      unused = await token();
    await revoke(db, "quartz");
    expect(await redeem(db, unused, null)).toBeNull();
    expect(await validateSession(db, id!)).toBeNull();
    expect(
      await db`SELECT * FROM control.audit_log WHERE action='showcase.revoke' AND actor=${`api-key:${invented.api_key_id}`}`,
    ).toHaveLength(1);
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", "[]");
    await expect(revoke(db, "quartz")).resolves.toBeUndefined();
  });
  it("removing a person refuses the next session request and outstanding link", async () => {
    const id = await redeem(db, await token(), null),
      unused = await token();
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", "[]");
    expect(await validateSession(db, id!)).toBeNull();
    expect(await redeem(db, unused, null)).toBeNull();
  });
  it("checks stored expiry and handle, and rolls back consumption when actor insertion fails", async () => {
    const t = await token(),
      link = verifyLink(t)!;
    expect(
      await redeem(db, signLink({ ...link, exp: link.exp + 120 }), null),
    ).toBeNull();
    await owner`UPDATE control.showcase_actor SET handle='another'`;
    await expect(redeem(db, t, null)).rejects.toThrow("another handle");
    expect(
      (await db`SELECT used_at FROM control.showcase_link`)[0]!.used_at,
    ).toBeNull();
    expect(await db`SELECT * FROM control.showcase_session`).toHaveLength(0);
  });
  it("keeps two tabs valid without rotating the session, then signs out", async () => {
    const id = (await redeem(db, await token(), null))!;
    const tabs = await Promise.all([
      validateSession(db, id),
      validateSession(db, id),
    ]);
    expect(tabs[0]?.id_hash).toBe(tabs[1]?.id_hash);
    expect(tabs[0]?.csrf_token).toBe(tabs[1]?.csrf_token);
    await signOut(db, tabs[0]!);
    expect(await validateSession(db, id)).toBeNull();
  });
  it("validates six requests while activity and actor rows are locked, then observes revocation", async () => {
    const id = (await redeem(db, await token(), null))!;
    await owner`UPDATE control.showcase_session SET last_seen_at=now()-interval '2 minutes'`;
    const [before] =
      await owner`SELECT last_seen_at, xmin::text AS version FROM control.showcase_session`;
    await owner.begin(async (tx) => {
      await tx`SELECT id_hash FROM control.showcase_session FOR UPDATE`;
      await tx`SELECT api_key_id FROM control.showcase_actor FOR UPDATE`;
      const requests = await Promise.all(
        Array.from({ length: 6 }, () => validateSession(db, id)),
      );
      expect(requests.every((session) => session?.id_hash === hash(id))).toBe(
        true,
      );
      expect(
        await tx`SELECT last_seen_at, xmin::text AS version FROM control.showcase_session`,
      ).toEqual([before]);
      // The held lock makes a blocking UPDATE fail at 250 ms. All six reads
      // complete before it is released; revocation is visible on the next read.
      await tx`UPDATE control.showcase_session SET revoked_at=now()`;
    });
    expect(await validateSession(db, id)).toBeNull();
  });
  it("samples activity once per minute without rewriting the actor", async () => {
    const id = (await redeem(db, await token(), null))!;
    const [actor] =
      await owner`SELECT xmin::text AS version FROM control.showcase_actor`;
    await owner`UPDATE control.showcase_session SET last_seen_at=now()-interval '2 minutes'`;
    const [before] =
      await owner`SELECT last_seen_at FROM control.showcase_session`;
    expect(await validateSession(db, id)).not.toBeNull();
    const [touched] =
      await owner`SELECT last_seen_at, xmin::text AS version FROM control.showcase_session`;
    expect(touched!.last_seen_at > before!.last_seen_at).toBe(true);
    await Promise.all(Array.from({ length: 6 }, () => validateSession(db, id)));
    expect(
      await owner`SELECT last_seen_at, xmin::text AS version FROM control.showcase_session`,
    ).toEqual([touched]);
    expect(
      await owner`SELECT xmin::text AS version FROM control.showcase_actor`,
    ).toEqual([actor]);
  });
  it("records a changed display name only when it changes", async () => {
    const id = (await redeem(db, await token(), null))!;
    vi.stubEnv(
      "MDP_SHOWCASE_PEOPLE",
      JSON.stringify([{ ...invented, display_name: "New label" }]),
    );
    expect((await validateSession(db, id))?.person.display_name).toBe(
      "New label",
    );
    expect(
      await db`SELECT display_name FROM control.showcase_actor`,
    ).toMatchObject([{ display_name: "New label" }]);
  });
  it("enforces idle and absolute expiry with database time", async () => {
    const idle = (await redeem(db, await token(), null))!;
    await db`UPDATE control.showcase_session SET last_seen_at=now()-interval '14 days' WHERE id_hash=${hash(idle)}`;
    expect(await validateSession(db, idle)).toBeNull();
    const absolute = (await redeem(db, await token(), null))!;
    await db`UPDATE control.showcase_session SET created_at=now()-interval '60 days' WHERE id_hash=${hash(absolute)}`;
    expect(await validateSession(db, absolute)).toBeNull();
  });
  it("uses configured expiry for insertion and rejects both exact boundaries", async () => {
    vi.stubEnv("MDP_SHOWCASE_IDLE_DAYS", "2");
    vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", "5");
    const id = (await redeem(db, await token(), null))!;
    expect(
      await db`SELECT expires_at-created_at = interval '5 days' AS matches FROM control.showcase_session`,
    ).toMatchObject([{ matches: true }]);
    await db`UPDATE control.showcase_session SET created_at=now()-interval '5 days'+interval '1 minute', last_seen_at=now()-interval '2 days'+interval '1 minute' WHERE id_hash=${hash(id)}`;
    expect(await validateSession(db, id)).not.toBeNull();
    await db`UPDATE control.showcase_session SET last_seen_at=now()-interval '2 days' WHERE id_hash=${hash(id)}`;
    expect(await validateSession(db, id)).toBeNull();
    await db`UPDATE control.showcase_session SET last_seen_at=now(), created_at=now()-interval '5 days' WHERE id_hash=${hash(id)}`;
    expect(await validateSession(db, id)).toBeNull();
  });
  it("does not extend a stored expiry when the configured maximum grows", async () => {
    const id = (await redeem(db, await token(), null))!;
    vi.stubEnv("MDP_SHOWCASE_MAX_DAYS", "90");
    await db`UPDATE control.showcase_session SET expires_at=now() WHERE id_hash=${hash(id)}`;
    expect(await validateSession(db, id)).toBeNull();
  });
  it("retains the old actor mapping after key rotation", async () => {
    const id = (await redeem(db, await token(), null))!;
    vi.stubEnv(
      "MDP_SHOWCASE_PEOPLE",
      JSON.stringify([
        {
          ...invented,
          api_key_id: "00000000-0000-4000-8000-000000000002",
          admin_key: "rotated-key",
        },
      ]),
    );
    expect((await validateSession(db, id))?.person.admin_key).toBe(
      "rotated-key",
    );
    const actors =
      await db`SELECT * FROM control.showcase_actor ORDER BY api_key_id`;
    expect(actors).toHaveLength(2);
    expect(actors[0]!.retired_at).not.toBeNull();
  });
  it("records a rotated key even when its first action is CLI revocation", async () => {
    await token();
    vi.stubEnv(
      "MDP_SHOWCASE_PEOPLE",
      JSON.stringify([
        { ...invented, api_key_id: "00000000-0000-4000-8000-000000000003" },
      ]),
    );
    await revoke(db, "quartz");
    expect(
      await db`SELECT api_key_id FROM control.showcase_actor`,
    ).toHaveLength(2);
    expect(
      (
        await db`SELECT actor FROM control.audit_log WHERE action='showcase.revoke'`
      )[0]!.actor,
    ).toBe("api-key:00000000-0000-4000-8000-000000000003");
  });
  it("only control_rt has runtime grants on showcase tables", async () => {
    const rows =
      await owner`SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_schema='control' AND table_name LIKE 'showcase_%'`;
    expect(
      rows.every((r) => ["migrator", "control_rt"].includes(r.grantee)),
    ).toBe(true);
    expect(
      await owner`SELECT has_table_privilege('control_rt','control.showcase_actor','DELETE') AS allowed`,
    ).toMatchObject([{ allowed: false }]);
  });
});
