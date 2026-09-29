import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { z } from 'zod';
import { required } from './config.js';
export const SESSION_COOKIE = '__Host-mdp_showcase';
export const PRE_COOKIE = '__Host-pre';
export const cookieOptions = { secure: true, httpOnly: true, path: '/', sameSite: 'lax' as const };
export const random = (bytes = 32) => randomBytes(bytes).toString('base64url');
export const hash = (value: string) => createHash('sha256').update(value).digest('hex');
export function equal(a: string, b: string): boolean {
  const aa = Buffer.from(a), bb = Buffer.from(b);
  return aa.length === bb.length && timingSafeEqual(aa, bb);
}
function signature(payload: string, key: string) {
  const secret = required(key);
  if (Buffer.byteLength(secret) < 32) throw new Error('Showcase signing secret is too short. Set at least 32 random bytes.');
  return createHmac('sha256', secret).update(payload).digest('base64url');
}
function sign(value: object, key: string): string {
  const payload = Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${payload}.${signature(payload, key)}`;
}
function verify(token: string, key: string): unknown {
  if (token.length > 2048) return null;
  const [payload, sig, extra] = token.split('.');
  if (!payload || !sig || extra || !equal(signature(payload, key), sig)) return null;
  try { return JSON.parse(Buffer.from(payload, 'base64url').toString()); } catch { return null; }
}
const linkSchema = z.object({ handle: z.string(), nonce: z.string().regex(/^[A-Za-z0-9_-]{22}$/), exp: z.number().int() });
export type Link = z.infer<typeof linkSchema>;
export const signLink = (link: Link) => sign(link, 'MDP_SHOWCASE_LINK_SECRET');
export function verifyLink(token: string, now = Date.now()): Link | null {
  const parsed = linkSchema.safeParse(verify(token, 'MDP_SHOWCASE_LINK_SECRET'));
  return parsed.success && parsed.data.exp * 1000 > now ? parsed.data : null;
}
const preSchema = z.object({ nonce: z.string().length(43), exp: z.number().int() });
export function validPre(token: string, now = Date.now()): boolean {
  const parsed = preSchema.safeParse(verify(token, 'MDP_SHOWCASE_SESSION_SECRET'));
  return parsed.success && parsed.data.exp * 1000 > now && parsed.data.exp * 1000 <= now + 600_000;
}
export function preAuthentication(existing?: string, now = Date.now()): string {
  return existing && validPre(existing, now) ? existing : sign({ nonce: random(), exp: Math.floor(now / 1000) + 600 }, 'MDP_SHOWCASE_SESSION_SECRET');
}
