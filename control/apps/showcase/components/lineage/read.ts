// A sheet retries once after the server's short rate window. A second refusal
// reaches the viewer's existing Retry state, so a loop cannot keep sending reads.
export async function readSheet(url: string, signal: AbortSignal) {
  let response = await fetch(url, { signal, cache: "no-store" });
  if (response.status === 429) {
    const seconds = Number(response.headers.get("Retry-After") ?? "1");
    const delay =
      Number.isFinite(seconds) && seconds >= 0
        ? Math.min(seconds, 60) * 1000
        : 1000;
    signal.throwIfAborted();
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(signal.reason);
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, delay);
      signal.addEventListener("abort", abort, { once: true });
    });
    response = await fetch(url, { signal, cache: "no-store" });
  }
  return response;
}
