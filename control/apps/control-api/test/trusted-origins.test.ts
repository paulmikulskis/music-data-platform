import { afterEach, expect, it, vi } from "vitest";
import { checkOrigin } from "../src/auth.js";
import { app } from "../src/app.js";
afterEach(()=>vi.unstubAllEnvs());
const request = (origin?: string) => new Request("http://control.internal/actions/probe",{method:"POST",headers:origin ? {origin} : {}});
it("accepts the request origin and exact configured origins",()=>{
 vi.stubEnv("MDP_TRUSTED_BROWSER_ORIGINS","https://showcase.example, https://second.example:8443");
 for(const origin of [undefined,"http://control.internal","https://showcase.example","https://second.example:8443"])
  expect(()=>checkOrigin(request(origin))).not.toThrow();
});
it("refuses suffixes, subdomains, ports, null and malformed configured entries",()=>{
 vi.stubEnv("MDP_TRUSTED_BROWSER_ORIGINS","https://showcase.example,https://bad.example/path,*,null");
 for(const origin of ["https://showcase.example.evil","https://sub.showcase.example","http://showcase.example","https://showcase.example:8443","https://bad.example","null"])
  expect(()=>checkOrigin(request(origin))).toThrow();
});
it("refuses cross-site browser POSTs before invoking a console action",async()=>{
 vi.stubEnv("MDP_AUTH_MODE","dev");vi.stubEnv("CLERK_SECRET_KEY","");
 vi.stubEnv("MDP_TRUSTED_BROWSER_ORIGINS","https://showcase.example");
 const response=await app.request(request("https://untrusted.example"));
 expect(response.status).toBe(403);
});
