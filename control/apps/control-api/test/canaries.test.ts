import { beforeEach, expect, it, vi } from "vitest";
import { createRouterClient } from "@orpc/server";
const service = vi.hoisted(()=>vi.fn());
const unsafe = vi.hoisted(()=>vi.fn(async(_query: string, _params?: unknown[])=>[]));
const rows = vi.hoisted(()=>vi.fn());
vi.mock("../src/service.js",()=>({service,serviceDeadline:{run:(_deadline:number,body:()=>unknown)=>body()}}));
vi.mock("../src/db.js",async()=>({...await vi.importActual("../src/db.js"),database:()=>({unsafe,begin:async(body:(db:unknown)=>unknown)=>body({unsafe})}),rows}));
import { router } from "../src/router.js";
const context = {identity:{actor:"fixture",admin:true,tenant_id:null,tenant_slug:null},db:{unsafe} as never};
const client = createRouterClient(router,{context});
const source = {source_key:"fixture",tenant_bound:false};
const passed = {status:"passed",error_class:null,records_validated:1,next_step:"Open /functions/fixture"};
beforeEach(()=>{
 vi.resetAllMocks();
 unsafe.mockResolvedValue([]);
 rows.mockImplementation(async(_db,_schema,query:string)=>query.includes("WHERE enabled") ? [source] : []);
 service.mockImplementation(async(path:string)=>path.endsWith("metadata_only=true") ? {manifest:{kind:"invoke",code_fingerprint:"changed"}} : passed);
});
it("calls the dry-run service and records only an audit through the router",async()=>{
 expect((await client.streamlines.canaries({})).results).toMatchObject([{source_key:"fixture:global",status:"passed"}]);
 expect(service).toHaveBeenCalledWith("/v1/functions/fixture/probe?scope=global",expect.anything(),{},undefined,expect.objectContaining({timeoutMs:expect.any(Number)}));
 expect(service.mock.calls.every(([path])=>path.includes("/functions/"))).toBe(true);
 expect(unsafe.mock.calls.every(([query])=>query.includes("control.audit_log"))).toBe(true);
 expect(unsafe).toHaveBeenCalledWith(expect.stringContaining("source.canary"),expect.arrayContaining(["fixture:global",expect.stringContaining('"records_validated":1')]));
});
it.each(["not_due","skipped"])("reports %s without an alert",async status=>{
 service.mockImplementation(async(path:string)=>path.endsWith("metadata_only=true") ? {manifest:{kind:"invoke",code_fingerprint:"changed"}} : {...passed,status,records_validated:0});
 expect((await client.streamlines.canaries({})).results[0]?.status).toBe(status);
 expect(service.mock.calls.some(([path])=>path.includes("alerts/"))).toBe(false);
});
it.each([["failed","schema_drift"],["timed_out","invoke_timeout"]])("alerts on %s without a run",async(status,error_class)=>{
 service.mockImplementation(async(path:string)=>path.endsWith("metadata_only=true") ? {manifest:{kind:"invoke",code_fingerprint:"changed"}} : {...passed,status,error_class,records_validated:0});
 expect((await client.streamlines.canaries({})).results[0]).toMatchObject({status,error_class});
 expect(service).toHaveBeenCalledWith("/v1/alerts/canary_failed",expect.anything(),{source_key:"fixture",scope:"global",run_id:null});
});
it("isolates a throwing source and failed audit without losing other outcomes",async()=>{
 rows.mockImplementation(async(_db,_schema,query:string)=>query.includes("WHERE enabled") ? [source,{...source,source_key:"broken"},source] : []);
 service.mockImplementation(async(path:string)=>{
   if(path.includes("/broken")) throw {error_class:"service_unreachable"};
   return path.endsWith("metadata_only=true") ? {manifest:{kind:"invoke",code_fingerprint:"changed"}} : passed;
 });
 unsafe.mockImplementation(async(query:string)=>{if(query.includes("'source.canary'")) throw new Error("audit unavailable"); return [];});
 expect((await client.streamlines.canaries({})).results.map(r=>r.status)).toEqual(["passed","failed","passed"]);
});
it("keeps outer failures advisory and logs a safe class and message",async()=>{
 rows.mockRejectedValue(new Error("secret-row-value"));
 const log=vi.spyOn(console,"warn").mockImplementation(()=>{});
 try {
   expect(await client.streamlines.canaries({})).toEqual({results:[]});
   expect(log).toHaveBeenCalledWith(expect.stringContaining("canary_check_failed"));
   expect(log.mock.calls.flat().join()).not.toContain("secret-row-value");
 } finally {log.mockRestore();}
});
it("returns skipped when no tenant is active",async()=>{
 rows.mockImplementation(async(_db,_schema,query:string)=>query.includes("WHERE enabled") ? [{...source,tenant_bound:true}] : []);
 expect((await client.streamlines.canaries({})).results[0]?.status).toBe("skipped");
 expect(service.mock.calls.some(([path])=>path.includes("/probe"))).toBe(false);
});
it("bounds the whole report and starts no run or cleanup",async()=>{
 let now=0;
 const clock=vi.spyOn(Date,"now").mockImplementation(()=>now);
 rows.mockImplementation(async(_db,_schema,query:string)=>query.includes("WHERE enabled") ? Array.from({length:6},()=>source) : []);
 service.mockImplementation(async(path:string)=>{
   if(path.endsWith("metadata_only=true")) return {manifest:{kind:"invoke",code_fingerprint:"changed"}};
   if(path.includes("/probe")) {now+=31000; throw {error_class:"invoke_timeout"};}
   return {};
 });
 try {
   expect((await client.streamlines.canaries({})).results.map(r=>r.status)).toEqual(["timed_out","timed_out","timed_out","skipped","skipped","skipped"]);
   expect(service.mock.calls.some(([path])=>path.includes("/runs/") || path.includes("/cycles/"))).toBe(false);
 } finally {clock.mockRestore();}
});
it.each(["passed","failed","not_due","skipped","timed_out"])("dryProbe preserves %s and audits the caller",async status=>{
 service.mockResolvedValue({...passed,status});
 expect(await client.streamlines.dryProbe({source_key:"fixture",scope:"tenant:00000000-0000-4000-8000-000000000001"})).toEqual({...passed,status});
 expect(service).toHaveBeenCalledWith("/v1/functions/fixture/probe?scope=tenant%3A00000000-0000-4000-8000-000000000001",expect.anything(),{},undefined,{timeoutMs:30000});
 expect(unsafe).toHaveBeenCalledWith(expect.stringContaining("INSERT INTO control.audit_log"),expect.arrayContaining(["fixture","streamlines.dryProbe"]));
 expect(unsafe).toHaveBeenCalledWith(expect.stringContaining("UPDATE control.audit_log"),expect.arrayContaining([expect.stringContaining('"records_validated":1')]));
});
it.each([["invoke_timeout","timed_out"],["service_unreachable","failed"]])("dryProbe maps %s",async(error_class,status)=>{
 service.mockRejectedValue({error_class});
 expect(await client.streamlines.dryProbe({source_key:"fixture"})).toEqual({status,error_class,records_validated:0,next_step:"Open /functions/fixture"});
});
it("dryProbe rejects invalid input and non-admin callers before service work",async()=>{
 await expect(client.streamlines.dryProbe({source_key:"../bad"})).rejects.toBeDefined();
 for(const identity of [{admin:false,staff:true},{admin:false,promoter:true},{admin:false}]){
  const denied=createRouterClient(router,{context:{...context,identity:{...context.identity,...identity}}});
  await expect(denied.streamlines.dryProbe({source_key:"fixture"})).rejects.toMatchObject({status:403});
 }
 expect(service).not.toHaveBeenCalled();
});
it("the console Probe form calls the dry adapter",async()=>{
 const {action}=await import("../src/pages.js");
 expect(await action(context,"probe",{source_key:"fixture",scope:"global"})).toEqual(passed);
 expect(service.mock.calls[0]?.[0]).toBe("/v1/functions/fixture/probe?scope=global");
});
it("retires the sample action without starting work or offering a retry", async () => {
  const { app } = await import("../src/app.js");
  const { Result, action } = await import("../src/pages.js");
  vi.stubEnv("MDP_AUTH_MODE", "dev");
  vi.stubEnv("CLERK_SECRET_KEY", "");
  rows.mockResolvedValue([]);
  try {
    const response = await app.request("/actions/run-sample", {
      method: "POST",
      body: new URLSearchParams({ source_key: "fixture", key: "fixture" }),
    });
    expect(response.status).toBe(410);
    const html = await response.text();
    expect(html).toContain("This action is no longer available.");
    expect(html).toContain('href="/functions"');
    expect(html).toContain("Choose Run now or Probe");
    await expect(action(context, "run-sample", { source_key: "fixture" }))
      .rejects.toThrow("Unknown action");
    expect(service).not.toHaveBeenCalled();
    expect(unsafe).not.toHaveBeenCalled();
    const result = String(await Result({ result: {
      action: "streamlines.probe",
      state: "failed",
      input: { source_key: "fixture", key: "fixture", scope: "global" },
      error_class: "service_unreachable",
    } }));
    expect(result).not.toContain("/actions/run-sample");
    expect(result).not.toContain("Retry action");
  } finally {
    vi.unstubAllEnvs();
  }
});
it("the CLI probe command dispatches to dryProbe",async()=>{
 const {serve}=await import("@hono/node-server");
 const {app}=await import("../src/app.js");
 const {execFile}=await import("node:child_process");
 const {promisify}=await import("node:util");
 vi.stubEnv("MDP_AUTH_MODE","dev");vi.stubEnv("CLERK_SECRET_KEY","");
 const server=serve({fetch:app.fetch,hostname:"127.0.0.1",port:0});
 try {
  if(!server.listening)await new Promise<void>(resolve=>server.once("listening",resolve));
  const address=server.address();if(!address||typeof address==="string")throw new Error("Use a TCP test listener");
  const {stdout}=await promisify(execFile)(process.execPath,["--import","tsx","packages/mdp-cli/src/index.ts","probe","fixture","--scope","global"],{env:{...process.env,MDP_CONTROL_API_URL:`http://127.0.0.1:${address.port}`,MDP_CONTROL_API_KEY:""}});
  expect(JSON.parse(stdout)).toEqual(passed);
  expect(service.mock.calls[0]?.[0]).toBe("/v1/functions/fixture/probe?scope=global");
 } finally {
  await new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()));vi.unstubAllEnvs();
 }
});
