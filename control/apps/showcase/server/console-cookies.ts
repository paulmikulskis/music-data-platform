// No upstream cookie enters the browser. Session hashes and cookie values stay on the server.
export class ConsoleCookies {
  private values = new Map<string, { cookie: string; expires: number }>();
  get(session: string) {
    const value = this.values.get(session);
    if (value && value.expires > Date.now()) return value.cookie;
    this.values.delete(session);
    return undefined;
  }
  save(session: string, cookies: string[]) {
    for (const cookie of cookies) {
      const match = /^mdp_workbench_session=([^;\s]*)/.exec(cookie);
      if (!match) continue;
      const age = /;\s*max-age=(-?\d+)/i.exec(cookie);
      const date = /;\s*expires=([^;]+)/i.exec(cookie);
      const expires = age ? Date.now() + Math.min(Number(age[1]), 86400) * 1000 : date ? Math.min(Date.parse(date[1]!), Date.now() + 86400000) : Date.now() + 86400000;
      if (!match[1] || expires <= Date.now()) { this.values.delete(session); continue; }
      for (const [key, value] of this.values) if (value.expires <= Date.now()) this.values.delete(key);
      if (!this.values.has(session) && this.values.size >= 256) this.values.delete(this.values.keys().next().value!);
      this.values.set(session, { cookie: `mdp_workbench_session=${match[1]}`, expires });
    }
  }
}
