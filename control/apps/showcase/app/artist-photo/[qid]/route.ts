import { session } from '../../../server/session';
import { artistPhoto, qidPattern } from '../../../server/artist-photo';
export const dynamic = 'force-dynamic';
// The photo only; the card shows it after its credit loads from /artist-photo/<item>/credit.
export async function GET(_request: Request, { params }: { params: Promise<{ qid: string }> }) {
  if (!await session()) return new Response('Open /sign-in.', { status: 401 });
  const { qid } = await params;
  if (!qidPattern.test(qid)) return new Response('Photo unavailable. Open the artist.', { status: 404 });
  const photo = await artistPhoto(qid).catch(() => null);
  if (!photo) return new Response('Photo unavailable. Open the artist.', { status: 404 });
  return new Response(new Uint8Array(photo.bytes), { headers: { 'Content-Type': photo.type, 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' } });
}
