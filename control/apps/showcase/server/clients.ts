import 'server-only';
import postgres from 'postgres';
import { createDataClient } from '@mdp/data-sdk';
import { contract } from '@mdp/contracts';
import { createORPCClient } from '@orpc/client';
import { RPCLink } from '@orpc/client/fetch';
import type { ContractRouterClient } from '@orpc/contract';
import { required, type Person } from '@mdp/showcase-auth';
let controlDB: ReturnType<typeof postgres> | undefined;
let warehouseDB: ReturnType<typeof postgres> | undefined;
export function controlStore() {
  return controlDB ??= postgres(required('MDP_CONTROL_RT_URL'), { max: 4, connect_timeout: 5, onnotice: () => {}, connection: { application_name: 'mdp-showcase-auth', statement_timeout: 5000, lock_timeout: 1000, transaction_timeout: 6000 } });
}
export function warehouse() {
  const url = new URL(required('MDP_SHOWCASE_WH_URL'));
  const local = ['localhost', '127.0.0.1'].includes(url.hostname);
  const mode = url.searchParams.get('sslmode');
  return warehouseDB ??= postgres(required('MDP_SHOWCASE_WH_URL'), {
    max: 4, connect_timeout: 5, ssl: !local || mode === 'require' ? 'require' : mode === 'prefer' ? 'prefer' : false,
    connection: { application_name: 'mdp-showcase', statement_timeout: 5000, lock_timeout: 1000, transaction_timeout: 6000, default_transaction_read_only: true },
  });
}
export function dataClient() {
  return createDataClient(required('MDP_DATA_API_URL'), { 'x-api-key': required('MDP_SHOWCASE_READER_KEY') });
}
export function controlClient(person: Person): ContractRouterClient<typeof contract> {
  return createORPCClient(new RPCLink({ url: new URL('/rpc', required('MDP_CONTROL_API_URL')).toString(),
    headers: { 'x-api-key': person.admin_key },
    fetch: (request, init) => fetch(request, { ...init, cache: 'no-store', signal: AbortSignal.timeout(6000) }),
  }));
}
