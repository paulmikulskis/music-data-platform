import { z } from "zod";
import { admission, poll, functionPage, runDto, jsonRow } from "../src/index.js";
export const consumed = [
  {path:"/v1/functions/{source_key}",method:"get",dto:functionPage},
  {path:"/v1/functions/{source_key}/run",method:"post",dto:admission},
  {path:"/v1/invoke",method:"post",dto:admission},
  {path:"/v1/runs/{run_id}",method:"get",dto:poll},
  {path:"/v1/runs",method:"get",dto:z.array(runDto)},
  {path:"/v1/runs/{run_id}/cancel",method:"post",dto:poll},
  {path:"/v1/bind_cycle",method:"post",dto:jsonRow},
  {path:"/v1/repair",method:"post",dto:jsonRow},
  {path:"/v1/backfill",method:"post",dto:admission},
  {path:"/v1/migrate",method:"post",dto:jsonRow},
  {path:"/v1/registry/sync",method:"post",dto:z.object({registered:z.number().int()})},
  {path:"/v1/dbt/webhook",method:"post",dto:admission},
  {path:"/v1/health/detail",method:"get",dto:jsonRow},
];
export const recording = z.object({path:z.string(),url:z.string(),method:z.string(),status:z.number(),request:z.unknown().optional(),response:z.unknown()});
