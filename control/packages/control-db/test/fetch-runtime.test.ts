import { describe, expect, it } from 'vitest';
import postgres from 'postgres';
import { randomUUID } from 'node:crypto';

const database = process.env.MDP_FETCH_TEST_DATABASE_URL;
describe.skipIf(!database)('fetch migration on isolated Postgres', () => {
  it('seeds the hosts with robots respected', async () => {
    const db = postgres(database!, { max: 1 });
    try {
      const hosts = await db`SELECT * FROM control.host_health ORDER BY host`;
      expect(hosts).toHaveLength(6);
      const audit = await db`SELECT subject, actor FROM control.audit_log WHERE action='robots_override'`;
      expect(audit).toEqual([]);
      expect(hosts.every(h => h.robots_policy === 'respect')).toBe(true);
    } finally { await db.end(); }
  });

  it('keeps health and knobs under separate column grants', async () => {
    const db = postgres(database!, { max: 1 });
    try {
      const [grants] = await db`
        SELECT has_column_privilege('functions_rt','control.host_health','blocked_until','UPDATE') AS health,
          has_column_privilege('functions_rt','control.host_health','host_rps','UPDATE') AS rate,
          has_column_privilege('control_rt','control.host_health','blocked_until','UPDATE') AS control_health,
          has_column_privilege('control_rt','control.host_health','host_rps','UPDATE') AS control_rate,
          has_column_privilege('functions_rt','control.streamline','transport_override','UPDATE') AS runtime_transport,
          has_column_privilege('control_rt','control.streamline','transport_override','UPDATE') AS control_transport`;
      expect(grants).toEqual({ health: true, rate: false, control_health: false, control_rate: true, runtime_transport: false, control_transport: true });
      const host = `test-${randomUUID()}.invalid`;
      await expect(db.begin(async tx => {
        await tx`INSERT INTO control.host_health(host,robots_policy) VALUES (${host},'override')`;
      })).rejects.toThrow(/host_health_override_reason/);
    } finally { await db.end(); }
  });
});
