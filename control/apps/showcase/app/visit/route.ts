import { session } from '../../server/session';
import { validMutation } from '@mdp/showcase-auth';
import { movers } from '../../server/reads';
import { recordVisit } from '../../server/visits';
export async function POST(request: Request) {
  const current = await session(); if (!current) return new Response('Open /sign-in.', {status:401});
  if (!validMutation(current,request.headers.get('origin'),request.headers.get('x-csrf-token') ?? '')) return new Response('Open /today and try again.',{status:403});
  const ranking = await movers().catch(()=>null); const close=ranking?.value.build.close_no;
  if (ranking?.value.build.stamped && close) await recordVisit(current,close);
  return new Response(null,{status:204});
}
