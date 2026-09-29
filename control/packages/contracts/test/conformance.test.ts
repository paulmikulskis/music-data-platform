import { describe, it, expect, beforeAll, afterEach, vi } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { z } from "zod";
import { OpenAPIGenerator } from "@orpc/openapi";
import { ZodToJsonSchemaConverter } from "@orpc/zod/zod4";
import { serviceRoutes, poll } from "../src/index.js";
import { consumed, recording } from "./consumed.js";
import { document, object, validator, compatible, type Document } from "./schema-compatibility.js";
import { normalizeService, service } from "../../../apps/control-api/src/service.js";
import { router } from "../../../apps/control-api/src/router.js";
import { app } from "../../../apps/control-api/src/app.js";
let serviceDoc: Document;
const controlDoc=JSON.parse(readFileSync(new URL("../openapi/control-api.json",import.meta.url),"utf8"));
const fixtureDir=new URL("./fixtures/",import.meta.url);
const fixtures=readdirSync(fixtureDir).filter(f=>f.endsWith(".json")).map(f=>recording.parse(JSON.parse(readFileSync(new URL(f,fixtureDir),"utf8"))));
beforeAll(async()=>{
  serviceDoc=document.parse(process.env.MDP_SERVICE_URL
    ? await (await fetch(`${process.env.MDP_SERVICE_URL}/v1/openapi.json`,{headers:{authorization:`Bearer ${process.env.MDP_SERVICE_TOKEN ?? ""}`}})).json()
    : JSON.parse(readFileSync(new URL("../../../../functions/openapi/service.json",import.meta.url),"utf8")));
});
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs();});
function responseSchema(route: Record<string,unknown>, status:number) {
  const responses=object.parse(route.responses);
  const response=object.parse(responses[String(status)]);
  return object.parse(object.parse(response.content)["application/json"]).schema;
}
describe("recorded service compatibility",()=>{
  it("records every consumed route",()=>expect(consumed.map(r=>`${r.method} ${r.path}`).sort()).toEqual(serviceRoutes.map(r=>`${r.method} ${r.path}`).sort()));
  for(const route of consumed) it(`${route.method.toUpperCase()} ${route.path}: request, response and consumer agree`, async()=>{
    const fixture=fixtures.find(f=>f.path===route.path&&f.method===route.method);
    expect(fixture, "Capture a real response for this route").toBeDefined();
    if(!fixture)throw new Error("Missing recorded response");
    const operation=serviceDoc.paths[route.path]?.[route.method];
    if(!operation)throw new Error("Route absent from service OpenAPI");
    const response=responseSchema(operation,fixture.status);
    const validate=validator(response,serviceDoc);
    expect(validate(fixture.response),JSON.stringify(validate.errors)).toBe(true);
    route.dto.parse(normalizeService(fixture.response));
    compatible(response,z.toJSONSchema(route.dto),serviceDoc);
    if(operation.requestBody) {
      const schema=object.parse(object.parse(object.parse(operation.requestBody).content)["application/json"]).schema;
      const validateRequest=validator(schema,serviceDoc);
      expect(validateRequest(fixture.request),JSON.stringify(validateRequest.errors)).toBe(true);
    }
    for(const parameter of z.array(object).parse(operation.parameters ?? [])) {
      const url=new URL(fixture.url,"http://fixture.invalid");
      const name=z.string().parse(parameter.name);
      const value=parameter.in === "query" ? url.searchParams.get(name) : parameter.in === "path" ? fixture.url.split("?")[0]?.split("/")[route.path.split("/").indexOf(`{${name}}`)] : null;
      if(value !== null && value !== undefined) {
        const schema=object.parse(parameter.schema);
        const typed=schema.type === "integer" ? Number(value) : value;
        expect(validator(schema,serviceDoc)(typed),name).toBe(true);
      } else if(parameter.required && parameter.in !== "header")throw new Error(`Missing required request parameter ${name}`);
    }
    if(fixture.status < 400) {
      vi.stubEnv("MDP_SERVICE_URL","http://fixture.invalid");vi.stubEnv("MDP_SERVICE_TOKEN","fixture");
      vi.stubGlobal("fetch",vi.fn(async()=>Response.json(fixture.response,{status:fixture.status})));
      expect(await service(fixture.url,route.dto,fixture.request)).toEqual(route.dto.parse(normalizeService(fixture.response)));
    }
  });
  it("rejects the formerly service-valid minimal poll and nested type/nullability drift",()=>{
    const minimal={run:{id:"00000000-0000-4000-8000-000000000001",status:"succeeded",coverage:"full",rows_written:"0",rows_rejected:"0",error_class:null,error_message:null,repairs_pending:0},receipts:[],repairs_pending:0};
    const schema=responseSchema(serviceDoc.paths["/v1/runs/{run_id}"]?.get ?? {},200);
    expect(validator(schema,serviceDoc)(minimal)).toBe(false);
    expect(()=>compatible({type:"object",required:[],properties:{}},z.toJSONSchema(poll),serviceDoc)).toThrow();
    expect(()=>compatible({anyOf:[{type:"string"},{type:"null"}]},{type:"string"},serviceDoc)).toThrow();
    expect(()=>compatible({type:"array",items:{type:"integer"}},{type:"array",items:{type:"string"}},serviceDoc)).toThrow();
  });
});
describe("control OpenAPI and adapters",()=>{
  it("checked-in OpenAPI exactly represents the implemented router",async()=>{
    const actual=await new OpenAPIGenerator({schemaConverters:[new ZodToJsonSchemaConverter()]}).generate(router,{info:{title:"MDP Control API",version:"1.0.0"},servers:[{url:"/api"}]});
    expect(actual).toEqual(controlDoc);
  });
  for(const adapter of ["api","rpc"]) it(`serves the complete consumed function response through ${adapter}`,async()=>{
    const fixture=fixtures.find(f=>f.path === "/v1/functions/{source_key}");if(!fixture)throw new Error("Missing fixture");
    vi.stubEnv("MDP_AUTH_MODE","dev");vi.stubEnv("CLERK_SECRET_KEY","");
    vi.stubEnv("MDP_CONTROL_RT_URL",process.env.MDP_CONTROL_RT_URL ?? "postgresql://localhost/unused");
    vi.stubEnv("MDP_SERVICE_URL","http://fixture.invalid");vi.stubEnv("MDP_SERVICE_TOKEN","fixture");
    vi.stubGlobal("fetch",vi.fn(async()=>Response.json(fixture.response)));
    const response=await app.request(adapter === "api" ? "/api/functions/fixture_accounts" : "/rpc/functions/page",adapter === "api" ? {} : {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({json:{source_key:"fixture_accounts"}})});
    expect(response.status).toBe(200);
    const body:unknown=await response.json();
    const value=adapter === "rpc" ? object.parse(body).json : body;
    const route=consumed[0];if(!route)throw new Error("Missing consumer");
    expect(route.dto.parse(value)).toEqual(route.dto.parse(normalizeService(fixture.response)));
    const operation=object.parse(object.parse(controlDoc.paths)["/functions/{source_key}"]).get;
    expect(validator(responseSchema(object.parse(operation),200),document.parse(controlDoc))(value)).toBe(true);
  });
});
