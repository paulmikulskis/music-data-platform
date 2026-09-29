// Local benchmark transport only. Product code has no fixture switch.
import { appendFileSync } from 'node:fs';
const original = globalThis.fetch;
globalThis.fetch = async function(input, init) {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (!['musicbrainz.org','coverartarchive.org'].includes(url.hostname)) return original(input, init);
  appendFileSync(process.env.SHOWCASE_ART_LOG, JSON.stringify({at:new Date().toISOString(),host:url.hostname})+'\n');
  await new Promise(resolve=>setTimeout(resolve,400));
  if(url.hostname==='musicbrainz.org')return Response.json({releases:[{id:'00000000-0000-4000-8000-000000000001',status:'Official'}]});
  return new Response(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=','base64'),{headers:{'content-type':'image/png'}});
};
