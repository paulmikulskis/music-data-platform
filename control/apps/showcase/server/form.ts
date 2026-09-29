import 'server-only';
// Bound bytes even when Content-Length is absent or dishonest.
export async function smallForm(request: Request): Promise<URLSearchParams | null> {
  if (request.headers.get('content-type')?.split(';')[0] !== 'application/x-www-form-urlencoded') return null;
  const reader = request.body?.getReader(); if (!reader) return null;
  let size = 0; const chunks: Uint8Array[] = [];
  for (;;) {
    const { done, value } = await reader.read(); if (done) break;
    size += value.byteLength;
    if (size > 8192) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  return new URLSearchParams(Buffer.concat(chunks).toString('utf8'));
}
