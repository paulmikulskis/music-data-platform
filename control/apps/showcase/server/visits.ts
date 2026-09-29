import 'server-only';
import type { Session } from '@mdp/showcase-auth';
import { controlStore } from './clients';
import { budget } from './read-budget';
export async function visitChanged(current: Session, close: string | null) {
  if (!close) return false;
  const rows = await budget.run('light', () => controlStore()<{ close_no: string }[]>`SELECT close_no::text FROM control.showcase_seen WHERE handle=${current.handle} AND scope='global'`);
  return !!rows[0] && BigInt(close) > BigInt(rows[0].close_no);
}
export async function recordVisit(current: Session, close: string) {
  await budget.run('light', () => controlStore().begin(async tx => {
    await tx`INSERT INTO control.showcase_seen (handle,scope,close_no,seen_at) VALUES (${current.handle},'global',${close},now()) ON CONFLICT (handle,scope) DO UPDATE SET close_no=GREATEST(showcase_seen.close_no,EXCLUDED.close_no),seen_at=now()`;
  }));
}
