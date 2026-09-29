import 'server-only';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { SESSION_COOKIE, validateSession } from '@mdp/showcase-auth';
import { controlStore } from './clients';
import { budget } from './read-budget';
export function sessionForToken(id: string | undefined) { return budget.run('essential', () => validateSession(controlStore(), id)); }
export async function session() { return sessionForToken((await cookies()).get(SESSION_COOKIE)?.value); }
export async function requireSession() {
  const current = await session();
  if (!current) redirect('/sign-in');
  return current;
}
