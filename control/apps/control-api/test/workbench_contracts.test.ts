import { it, expect } from "vitest";
import { QueryResult, PreviewResult, BacktestResult, ExplainResult, AsyncStatus } from "@mdp/contracts";
  it("exports all response DTOs independently of execution", () => {
    const preview={columns:[],rows:[],compiledSql:"select 1",upstream:[],timingMs:0,artifactRef:"fixture"};
    expect(QueryResult.safeParse({columns:[],rows:[],timingMs:0,plan:null}).success).toBe(true);
    expect(PreviewResult.safeParse(preview).success).toBe(true);
    expect(BacktestResult.safeParse({buildA:preview,buildB:preview,keyColumns:[],comparedColumns:[],added:[],removed:[],changed:[],summary:{}}).success).toBe(true);
    expect(ExplainResult.safeParse({compiledSql:"select 1",upstream:[],downstream:[],sourceFreshness:[],producingRuns:[]}).success).toBe(true);
    expect(AsyncStatus.safeParse({runId:"00000000-0000-4000-8000-000000000001",status:"queued",progress:0,error:null}).success).toBe(true);
    expect(QueryResult.safeParse({columns:[{name:7,type:"text",nullable:false,source:null}],rows:[],timingMs:0,plan:null}).success).toBe(false);
  });
