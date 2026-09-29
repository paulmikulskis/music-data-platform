// Next hands a page's dynamic segment over still percent-encoded, so a link to
// /s/song/spotify%3A6eVo… arrives as `spotify%3A6eVo…`. Every song key reads decoded, once;
// a key is never allowed a literal "%", so decoding an already plain key changes nothing.
// A malformed escape is no key at all.
export function routeKey(raw: string) {
  try {
    const key = decodeURIComponent(raw);
    return key && !key.includes("%") ? key : null;
  } catch {
    return null;
  }
}
