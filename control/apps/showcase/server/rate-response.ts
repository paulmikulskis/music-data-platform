import "server-only";
import { NextResponse } from "next/server";
import { errorCatalog } from "@mdp/contracts";
import type { Bucket } from "../lib/request-bucket";

export function rateResponse(bucket: Exclude<Bucket, "auth">) {
  const retryAfter = 1;
  const headers = {
    "Retry-After": String(retryAfter),
    "Cache-Control": "no-store",
  };
  const error = errorCatalog.showcase_rate_limited;
  if (bucket !== "document") {
    return NextResponse.json(
      {
        error_class: "showcase_rate_limited",
        message: error.summary,
        next_step: error.next_step,
      },
      { status: 429, headers },
    );
  }
  // This page needs no app shell. A tab remembers its one automatic retry until
  // a successful app page clears the marker; repeated refusals cannot reload forever.
  return new NextResponse(
    `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Wait a second · Music Data Platform</title>
<style>
@font-face{font-family:Display;src:url('/fonts/IBMPlexSans.ttf')}
@font-face{font-family:Body;src:url('/fonts/IBMPlexSans.ttf')}
*{box-sizing:border-box}body{margin:0;min-height:100svh;display:grid;place-items:center;
background:radial-gradient(ellipse at 30% 30%,#23343d,#142026 55%,#0e171c);color:#edf4f6;font:17px Body,system-ui,sans-serif}
main{width:min(100%,32rem);padding:2rem}h1{font:400 clamp(2.5rem,10vw,4rem) Display,Georgia,serif;margin:1rem 0}
p{color:#b4c8d0;line-height:1.5}button{font:inherit;color:inherit;background:transparent;border:1px solid #edf4f640;
border-radius:2rem;padding:.65rem 1.4rem;cursor:pointer}button:focus-visible{outline:2px solid #e8b16d;outline-offset:4px}
</style></head><body><main aria-labelledby="title"><h1 id="title">wait a second.</h1>
<p id="retry-message">This page is busy. It retries once in a second.</p>
<button id="reload" type="button">Reload</button>
<noscript><p>Reload this page after a second.</p></noscript>
</main><script>
const marker = 'showcase-rate-retry';
let timer;
document.getElementById('reload').onclick = () => {
  clearTimeout(timer);
  try { sessionStorage.removeItem(marker); } catch {}
  location.reload();
};
try {
  if (!sessionStorage.getItem(marker)) {
    sessionStorage.setItem(marker, '1');
    timer = setTimeout(() => location.reload(), ${retryAfter * 1000});
  } else {
    document.getElementById('retry-message').textContent = 'This page is still busy. Wait a second, then choose Reload.';
  }
} catch {
  document.getElementById('retry-message').textContent = 'This page is busy. Wait a second, then choose Reload.';
}
</script></body></html>`,
    {
      status: 429,
      headers: { ...headers, "Content-Type": "text/html; charset=utf-8" },
    },
  );
}
