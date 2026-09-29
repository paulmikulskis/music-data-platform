import type { Sql, TransactionSql } from "postgres";
import { person, origin, sessionLifetimes, type Person } from "./config.js";
import {
  equal,
  hash,
  random,
  signLink,
  verifyLink,
  type Link,
} from "./tokens.js";
export const noLink =
  "Open your sign-in link to continue. If you do not have one, ask the platform operator.";
export const ended =
  "Your sign-in ended. Ask the platform operator for a new link.";
export const expired = "This link has expired. Ask for a new one.";
export const timedOut =
  "This sign-in page timed out. Open the link from your message again.";
export const refused =
  "This request could not be checked. Open Today and try again.";
type DB = Sql | TransactionSql;
export async function recordActor(db: Pick<Sql, "unsafe">, p: Person) {
  const rows = await db.unsafe(
    `INSERT INTO control.showcase_actor (api_key_id, handle, display_name)
     VALUES ($1, $2, $3) ON CONFLICT (api_key_id) DO UPDATE
     SET retired_at = NULL, display_name = EXCLUDED.display_name
     WHERE showcase_actor.handle = EXCLUDED.handle RETURNING api_key_id`,
    [p.api_key_id, p.handle, p.display_name],
  );
  if (rows.length !== 1)
    throw new Error(
      "Admin key belongs to another handle. Assign a separate key in MDP_SHOWCASE_PEOPLE.",
    );
  await db.unsafe(
    "UPDATE control.showcase_actor SET retired_at = coalesce(retired_at, now()) WHERE handle = $1 AND api_key_id <> $2",
    [p.handle, p.api_key_id],
  );
}
async function audit(
  db: DB,
  p: Pick<Person, "api_key_id" | "handle">,
  action: string,
) {
  await db`INSERT INTO control.audit_log (actor, action, subject) VALUES (${`api-key:${p.api_key_id}`}, ${action}, ${p.handle})`;
}
// Mint/revoke and redemption serialize on the handle, including sessions inserted concurrently with revoke.
async function lock(db: DB, handle: string) {
  await db`SELECT pg_advisory_xact_lock(hashtextextended(${`showcase:${handle}`}, 0))`;
}
export function prepareLink(handle: string, ttlSeconds = 86400) {
  const p = person(handle);
  if (!p)
    throw new Error(
      "Handle is not configured. Check MDP_SHOWCASE_PEOPLE and try again.",
    );
  if (
    !Number.isInteger(ttlSeconds) ||
    ttlSeconds < 60 ||
    ttlSeconds > 86400 * 7
  )
    throw new Error("Link lifetime is out of range. Use 1m through 7d.");
  const link: Link = {
    handle,
    nonce: random(16),
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
  };
  return { p, link, url: `${origin()}/sign-in?t=${signLink(link)}` };
}
export async function mint(
  db: Sql,
  handle: string,
  ttlSeconds = 86400,
): Promise<string> {
  const { p, link, url } = prepareLink(handle, ttlSeconds);
  await db.begin(async (tx) => {
    await lock(tx, handle);
    await recordActor(tx, p);
    await tx`INSERT INTO control.showcase_link (nonce, handle, expires_at, created_by)
      VALUES (${link.nonce}, ${handle}, ${new Date(link.exp * 1000)}, ${`api-key:${p.api_key_id}`})`;
    await audit(tx, p, "showcase.link");
  });
  return url;
}
export async function redeem(
  db: Sql,
  token: string,
  userAgent: string | null,
): Promise<string | null> {
  const { maxDays } = sessionLifetimes();
  const link = verifyLink(token);
  if (!link) return null;
  const p = person(link.handle);
  if (!p) return null;
  return db.begin(async (tx) => {
    await lock(tx, link.handle);
    if (!person(link.handle)) return null;
    const used = await tx`UPDATE control.showcase_link SET used_at = now()
      WHERE nonce = ${link.nonce} AND handle = ${link.handle} AND used_at IS NULL AND revoked_at IS NULL
      AND expires_at > now() AND abs(extract(epoch FROM expires_at) - ${link.exp}) <= 60 RETURNING nonce`;
    if (used.length !== 1) return null;
    const id = random();
    await recordActor(tx, p);
    await tx`INSERT INTO control.showcase_session (id_hash, handle, csrf_token, expires_at, user_agent)
      VALUES (${hash(id)}, ${p.handle}, ${random()}, now() + ${maxDays} * interval '1 day', ${userAgent?.slice(0, 512) ?? null})`;
    await audit(tx, p, "showcase.sign_in");
    return id;
  });
}
export type Session = {
  id_hash: string;
  handle: string;
  csrf_token: string;
  person: Person;
};
// Activity is sampled at most once a minute. Validity is read on every request.
export const sessionTouchSeconds = 60;
export async function validateSession(
  db: Sql,
  id: string | undefined,
): Promise<Session | null> {
  const { idleDays, maxDays } = sessionLifetimes();
  if (!id || !/^[A-Za-z0-9_-]{43}$/.test(id)) return null;
  const current = await db<
    { handle: string; csrf_token: string; touch_due: boolean }[]
  >`SELECT handle, csrf_token,
    last_seen_at < now() - ${sessionTouchSeconds} * interval '1 second' AS touch_due
    FROM control.showcase_session
    WHERE id_hash = ${hash(id)} AND revoked_at IS NULL AND expires_at > now()
    AND created_at > now() - ${maxDays} * interval '1 day' AND last_seen_at > now() - ${idleDays} * interval '1 day'`;
  const row = current[0];
  const p = row && person(row.handle);
  if (!row || !p) return null;
  const actor = await db`SELECT 1 FROM control.showcase_actor
    WHERE api_key_id = ${p.api_key_id} AND handle = ${p.handle}
    AND display_name = ${p.display_name} AND retired_at IS NULL`;
  if (!actor.length) await db.begin((tx) => recordActor(tx, p));
  if (row.touch_due) {
    // Another tab or revocation can hold the row. Skip it instead of queuing;
    // the next request checks validity again and may record activity.
    await db`WITH due AS (
      SELECT id_hash FROM control.showcase_session
      WHERE id_hash = ${hash(id)} AND revoked_at IS NULL AND expires_at > now()
      AND created_at > now() - ${maxDays} * interval '1 day'
      AND last_seen_at > now() - ${idleDays} * interval '1 day'
      AND last_seen_at < now() - ${sessionTouchSeconds} * interval '1 second'
      FOR UPDATE SKIP LOCKED
    ) UPDATE control.showcase_session s SET last_seen_at = now()
      FROM due WHERE s.id_hash = due.id_hash`;
  }
  return {
    handle: row.handle,
    csrf_token: row.csrf_token,
    id_hash: hash(id),
    person: p,
  };
}
export function validMutation(
  session: Session,
  requestOrigin: string | null,
  csrf: string,
): boolean {
  return requestOrigin === origin() && equal(session.csrf_token, csrf);
}
export async function signOut(db: Sql, session: Session) {
  await db.begin(async (tx) => {
    await tx`UPDATE control.showcase_session SET revoked_at = now() WHERE id_hash = ${session.id_hash}`;
    await audit(tx, session.person, "showcase.sign_out");
  });
}
export async function revoke(db: Sql, handle: string) {
  await db.begin(async (tx) => {
    await lock(tx, handle);
    const p = person(handle);
    if (p) await recordActor(tx, p);
    const historical = p
      ? []
      : await tx<
          { api_key_id: string; display_name: string }[]
        >`SELECT api_key_id, display_name FROM control.showcase_actor WHERE handle = ${handle} ORDER BY first_seen_at DESC LIMIT 1`;
    const identity = p ?? (historical[0] ? { ...historical[0], handle } : null);
    if (!identity)
      throw new Error(
        "Handle has no showcase access. Check MDP_SHOWCASE_PEOPLE and try again.",
      );
    await tx`UPDATE control.showcase_link SET revoked_at = now() WHERE handle = ${handle} AND used_at IS NULL AND revoked_at IS NULL`;
    await tx`UPDATE control.showcase_session SET revoked_at = now() WHERE handle = ${handle} AND revoked_at IS NULL`;
    await audit(tx, identity, "showcase.revoke");
  });
}
