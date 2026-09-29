import { session } from '../../../../server/session';
import { artistPhoto, qidPattern } from '../../../../server/artist-photo';
export const dynamic = 'force-dynamic';
// The photo's author, license and Commons page. No credit means no photo.
export async function GET(_request: Request, { params }: { params: Promise<{ qid: string }> }) {
  if (!await session()) return Response.json({ next_step: 'Open /sign-in.' }, { status: 401 });
  const { qid } = await params;
  const photo = qidPattern.test(qid) ? await artistPhoto(qid).catch(() => null) : null;
  if (!photo) return Response.json({ credit: null, next_step: 'Open the artist.' }, { status: 404, headers: { 'Cache-Control': 'private, no-store' } });
  return Response.json({ credit: photo.credit }, { headers: { 'Cache-Control': 'private, no-store' } });
}
