import { afterEach, expect, it, vi } from 'vitest';
const clerk = vi.hoisted(() => ({ authenticateRequest: vi.fn(), users: {getUser: vi.fn()} }));
vi.mock('@clerk/backend', () => ({createClerkClient: () => clerk}));
import { authenticate } from '../src/auth.js';
afterEach(() => vi.unstubAllEnvs());
it('uses Clerk staff metadata with admin precedence and an operator-set warehouse mapping', async () => {
  vi.stubEnv('CLERK_SECRET_KEY','fixture');
  clerk.authenticateRequest.mockResolvedValue({toAuth: () => ({userId:'user_fixture'})});
  clerk.users.getUser.mockResolvedValue({publicMetadata:{mdp_staff:true,mdp_warehouse_role:'analyst_fixture'}});
  expect(await authenticate(new Request('http://local/workbench'))).toMatchObject({admin:false,staff:true,warehouse_role:'analyst_fixture'});
  clerk.users.getUser.mockResolvedValue({publicMetadata:{mdp_staff:true,mdp_admin:true}});
  expect(await authenticate(new Request('http://local/workbench'))).toMatchObject({admin:true,staff:false});
  clerk.users.getUser.mockResolvedValue({publicMetadata:{}});
  expect(await authenticate(new Request('http://local/workbench'))).toMatchObject({admin:false,staff:false});
});
