import { describe, expect, it, vi } from 'vitest';
import { createRouterClient } from '@orpc/server';
import { impl, type Context } from '../src/router.shared.js';
import { workbenchRouter } from '../src/workbench-router.js';

vi.mock('../src/db.js', async original => ({
  ...await original<typeof import('../src/db.js')>(),
  database: () => ({ unsafe: vi.fn().mockResolvedValue([]) }),
}));

const context: Context = {
  identity: { actor: 'staff:guard-test', admin: false, staff: true, tenant_id: null, tenant_slug: null },
  db: { unsafe: vi.fn() },
};

describe('staff procedure guard', () => {
  it('permits a GET through a direct caller', async () => {
    const read = impl.tenants.list.handler(() => []);
    const client = createRouterClient({ read }, { context });
    expect(await client.read({})).toEqual([]);
  });
  it('refuses an analysis procedure changed to DELETE through its own caller', async () => {
    const query = workbenchRouter.query;
    const mutation = { ...query, '~orpc': {
      ...query['~orpc'], route: { ...query['~orpc'].route, method: 'DELETE' as const },
    } };
    const client = createRouterClient({ mutation }, { context });
    await expect(client.mutation({ sessionId: 'f461f1e3-6bcf-4a42-a743-b0dba4b63a32', model: 'mart_draft', sql: 'select 1' }))
      .rejects.toMatchObject({ status: 403, data: { error_class: 'forbidden', next_step: expect.any(String) } });
  });
  it.each(['PUT', 'PATCH', 'DELETE', 'HEAD', undefined] as const)(
    'refuses a %s procedure even at an analysis path', async method => {
      const handler = vi.fn(() => []);
      const read = impl.tenants.list.handler(handler);
      // A future contract can change its method without changing the middleware.
      const mutation = { ...read, '~orpc': {
        ...read['~orpc'], route: { ...(method ? { method } : {}), path: '/workbench/query' as const },
      } };
      const client = createRouterClient({ mutation }, { context });
      await expect(client.mutation({})).rejects.toMatchObject({
        status: 403, data: { error_class: 'forbidden', next_step: expect.any(String), runbook: '/runbooks/forbidden' },
      });
      expect(handler).not.toHaveBeenCalled();
    },
  );
});
