import { cookies } from 'next/headers';
import { SESSION_COOKIE } from '@mdp/showcase-auth';
import { session, sessionForToken } from '../../server/session';
import { heartbeat } from '../../server/heartbeat';
export const dynamic = 'force-dynamic';
export async function HEAD() { return new Response(null, { status: await session() ? 204 : 401, headers: { 'Cache-Control': 'no-store' } }); }
export async function GET(request: Request) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const current = await sessionForToken(token);
  if (!current) return new Response('Open /sign-in.', { status: 401 });
  const raw = request.headers.get('last-event-id') ?? new URL(request.url).searchParams.get('after') ?? '';
  const after = /^[A-Za-z0-9_-]{1,2048}$/.test(raw) ? raw : '';
  let cleanup: (close: boolean) => void = () => {};
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false;
      const encode = new TextEncoder();
      const stop = (close = true) => { if (closed) return; closed = true; unsubscribe(); clearInterval(timer); if (close) controller.close(); };
      const unsubscribe = heartbeat.subscribe({ person: current.person, send(event, online, runner) {
        if (!closed) controller.enqueue(encode.encode(`${event ? `id: ${event.id}\n` : ''}event: ${event ? 'pulse' : 'connection'}\ndata: ${JSON.stringify(event ?? { online, runner })}\n\n`));
      } }, after);
      const timer = setInterval(async () => {
        try {
          const valid = await sessionForToken(token);
          if (!valid) { controller.enqueue(encode.encode('event: expired\ndata: {}\n\n')); stop(); }
        } catch { stop(); }
      }, 60000);
      cleanup = stop; request.signal.addEventListener('abort', () => stop(), { once: true });
    }, cancel() { cleanup(false); },
  });
  return new Response(stream, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8', 'Content-Encoding': 'identity', 'Cache-Control': 'no-store', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no' } });
}
