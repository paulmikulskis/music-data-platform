import { describe, it, expect, vi, afterEach } from 'vitest';
import { app } from '../src/app.js';
afterEach(()=>vi.unstubAllEnvs());
describe('page authentication',()=>{
 it('provides branded navigation for an unhandled page error',async()=>{
  vi.stubEnv('MDP_AUTH_MODE','');vi.stubEnv('CLERK_SECRET_KEY','');
  const response=await app.request('/functions/targets_export');
  expect(response.status).toBe(401);
  expect(response.headers.get('content-type')).toContain('text/html');
  expect(await response.text()).toContain('Ops →');
 });
});
