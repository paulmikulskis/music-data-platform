import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
vi.mock('server-only', () => ({}));
const mocked = vi.hoisted(() => ({ session: vi.fn(), signOut: vi.fn() }));
vi.mock('../server/session', () => ({ session: mocked.session }));
vi.mock('../server/clients', () => ({ controlStore: () => ({}) }));
vi.mock('@mdp/showcase-auth', async original => ({ ...await original<typeof import('@mdp/showcase-auth')>(), signOut: mocked.signOut }));
import { POST } from '../app/sign-out/route';
beforeEach(() => {
  vi.stubEnv('MDP_SHOWCASE_ORIGIN', 'https://mdp-showcase.example.invalid');
  mocked.signOut.mockReset(); mocked.session.mockResolvedValue({ csrf_token: 'session-csrf' });
});
afterEach(() => vi.unstubAllEnvs());
const request = (origin: string, csrf: string) => new NextRequest('https://mdp-showcase.example.invalid/sign-out', { method: 'POST', headers: { origin }, body: new URLSearchParams({ csrf }) });
it('refuses missing session CSRF or foreign origin without revoking', async () => {
  expect((await POST(request('https://other.invalid', 'session-csrf'))).status).toBe(403);
  expect((await POST(request('https://mdp-showcase.example.invalid', ''))).status).toBe(403);
  expect(mocked.signOut).not.toHaveBeenCalled();
});
it('revokes before clearing the cookie and browser storage', async () => {
  const result = await POST(request('https://mdp-showcase.example.invalid', 'session-csrf'));
  expect(mocked.signOut).toHaveBeenCalledOnce(); expect(result.status).toBe(303);
  expect(result.headers.get('set-cookie')).toContain('Max-Age=0');
  expect(result.headers.get('clear-site-data')).toContain('storage');
});
