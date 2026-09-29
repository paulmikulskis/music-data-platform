// Exercise the production router and service wrapper; only network I/O and its clock are simulated.
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createRouterClient } from "@orpc/server";
import { z } from "zod";
const unsafe = vi.hoisted(()=>vi.fn(async()=>[]));
const discovery = vi.hoisted(()=>({count:1}));
vi.mock("../src/db.js",async()=>({...await vi.importActual("../src/db.js"),
 database:()=>({unsafe,begin:async(body:(db:unknown)=>unknown)=>body({unsafe})}),
 rows:vi.fn(async(_db,_schema,query:string)=>query.includes("WHERE enabled") ? Array.from({length:discovery.count},()=>({source_key:"am_playlist",tenant_bound:false})) : []),
}));
import { router } from "../src/router.js";
import { service, serviceDeadline } from "../src/service.js";
import { canaryTimeoutMs, canaryHttpTimeoutMs } from "../src/health-policy.generated.js";
const client=createRouterClient(router,{context:{identity:{actor:"fixture",admin:true,tenant_id:null,tenant_slug:null},db:{} as never}});
const page={manifest:{kind:"invoke",code_fingerprint:"fixture"},last_runs:[],receipts:[],output_preview:[],rejected_sample:[],row_counts:[],fingerprint_history:[],log_url:"/functions/am_playlist/logs"};
let delay=0;
let metadataDelay=0;
let timeoutResult=false;
let bodyStalls=false;
const calls:string[]=[];
const limits:number[]=[];
beforeEach(()=>{
 vi.useFakeTimers();vi.setSystemTime(0);calls.length=0;limits.length=0;unsafe.mockClear();
 delay=0;metadataDelay=0;discovery.count=1;timeoutResult=false;bodyStalls=false;
 vi.stubEnv("MDP_SERVICE_URL","http://functions.fixture");vi.stubEnv("MDP_SERVICE_TOKEN","fixture");
 vi.spyOn(AbortSignal,"timeout").mockImplementation(ms=>{
  limits.push(ms);const controller=new AbortController();
  setTimeout(()=>controller.abort(new DOMException("fixture deadline","TimeoutError")),ms);
  return controller.signal;
 });
 vi.stubGlobal("fetch",vi.fn(async(url:URL,init:RequestInit)=>{
  calls.push(url.pathname);
  if(url.searchParams.has("metadata_only")){
   await new Promise(resolve=>setTimeout(resolve,metadataDelay));
   return Response.json(page);
  }
  if(url.pathname.includes("/alerts/"))return Response.json({status:"recorded"});
  const completed={status:timeoutResult?"timed_out":"passed",error_class:timeoutResult?"invoke_timeout":null,records_validated:timeoutResult?0:1,next_step:"Open /functions/am_playlist"};
  const pending=()=>new Promise<Response>((resolve,reject)=>{
   init.signal!.addEventListener("abort",()=>reject(init.signal!.reason),{once:true});
   setTimeout(()=>resolve(Response.json(completed)),delay);
  });
  if(bodyStalls)return {ok:true,json:async()=>{await pending();return completed;}} as Response;
  return pending();
 }));
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.unstubAllEnvs();vi.useRealTimers();});
it("accepts a probe that completes after the old 20-second cutoff",async()=>{
 delay=canaryTimeoutMs-1000;
 const result=client.streamlines.canaries({});
 await vi.advanceTimersByTimeAsync(delay);
 expect((await result).results[0]?.status).toBe("passed");
 expect(limits).toContain(canaryHttpTimeoutMs);
 expect(canaryHttpTimeoutMs).toBeGreaterThan(canaryTimeoutMs);
 expect(calls.some(path=>path.includes("/alerts/"))).toBe(false);
});
it("preserves the service's deadline outcome",async()=>{
 delay=canaryTimeoutMs;timeoutResult=true;
 const result=client.streamlines.canaries({});
 await vi.advanceTimersByTimeAsync(delay);
 expect((await result).results[0]).toMatchObject({status:"timed_out",error_class:"invoke_timeout"});
 expect(calls).toContain("/v1/alerts/canary_failed");
});
it.each([false,true])("classifies a client timeout as timed_out, including response body=%s",async stall=>{
 delay=canaryHttpTimeoutMs+1000;bodyStalls=stall;
 const result=client.streamlines.canaries({});
 await vi.advanceTimersByTimeAsync(canaryHttpTimeoutMs);
 expect((await result).results[0]).toMatchObject({status:"timed_out",error_class:"invoke_timeout"});
 expect(calls).toContain("/v1/alerts/canary_failed");
});
it("classifies an expired caller budget without dispatch",async()=>{
 await expect(serviceDeadline.run(0,()=>service("/expired",z.unknown()))).rejects.toMatchObject({error_class:"invoke_timeout"});
 expect(calls).toHaveLength(0);
});
it("keeps connection refusal distinct from timeout",async()=>{
 vi.stubGlobal("fetch",vi.fn(async()=>{throw new TypeError("connection refused");}));
 await expect(service("/refused",z.unknown())).rejects.toMatchObject({error_class:"service_unreachable"});
});

it("skips after metadata consumes the remaining full probe allowance",async()=>{
 discovery.count=3;metadataDelay=19000;delay=canaryTimeoutMs-1000;
 const result=client.streamlines.canaries({});
 await vi.advanceTimersByTimeAsync(120000);
 expect((await result).results.map(row=>row.status)).toEqual(["passed","passed","skipped"]);
 expect(calls.filter(path=>path.endsWith("/probe"))).toHaveLength(2);
 expect(calls.some(path=>path.includes("/alerts/"))).toBe(false);
});
it("dryProbe allows 25 seconds and maps the 30-second client deadline",async()=>{
 delay=24000;
 const passed=client.streamlines.dryProbe({source_key:"am_playlist"});
 await vi.advanceTimersByTimeAsync(delay);
 expect((await passed).status).toBe("passed");
 delay=31000;
 const timed=client.streamlines.dryProbe({source_key:"am_playlist"});
 await vi.advanceTimersByTimeAsync(30000);
 expect(await timed).toMatchObject({status:"timed_out",error_class:"invoke_timeout",records_validated:0});
});
