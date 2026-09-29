import { ResultLabel } from '@mdp/data-sdk';
import { oc } from '@orpc/contract';
import { z } from 'zod';
const row = z.record(z.string(), z.unknown());
const column = z.object({name:z.string(),type:z.string(),nullable:z.boolean(),source:z.string().nullable()});
export const QueryResult = z.object({labels:ResultLabel.optional(),columns:z.array(column),rows:z.array(row),timingMs:z.number(),plan:z.unknown()});
export const PreviewResult = z.object({labels:ResultLabel.optional(),columns:z.array(column),rows:z.array(row),truncated:z.boolean().optional(),totalRows:z.number().int().nonnegative().optional(),compiledSql:z.string(),upstream:z.array(z.string()),timingMs:z.number(),artifactRef:z.string(),invokeSkipped:z.boolean().optional()});
export const BacktestResult = z.object({buildA:PreviewResult,buildB:PreviewResult,keyColumns:z.array(z.string()),comparedColumns:z.array(z.string()),added:z.array(row),removed:z.array(row),changed:z.array(z.object({before:row,after:row})),summary:row});
export const ExplainResult = z.object({compiledSql:z.string(),upstream:z.array(z.string()),downstream:z.array(z.string()),sourceFreshness:z.array(row),producingRuns:z.array(row)});
export const AsyncStatus = z.object({runId:z.uuid(),status:z.string(),progress:z.number(),durationMs:z.number().nullable().optional(),error:z.object({error_class:z.string(),message:z.string()}).nullable()});
const session=z.object({sessionId:z.uuid()});
const model=session.extend({model:z.string().regex(/^[a-z][a-z0-9_]*$/),sql:z.string().min(1).max(100000).optional()});
const run=z.object({runId:z.uuid()});
function post<I extends z.ZodType,O extends z.ZodType>(path:`/${string}`,input:I,output:O){return oc.errors({SERVICE:{data:z.object({error_class:z.string(),message:z.string()})}}).route({method:'POST',path}).input(input).output(output);}
export const workbenchContract={
 sandboxStatus:post('/workbench/sandboxStatus',z.object({}),z.object({sandboxes:z.array(row)})),
 sandboxAction:post('/workbench/sandboxAction',z.object({schema:z.string().max(63),action:z.enum(['freeze','unfreeze','disable'])}),z.object({message:z.string()})),
 queries:post('/workbench/queries',z.object({}),z.object({queries:z.array(row)})),
 createSession:post('/workbench/createSession',z.object({}),z.object({sessionId:z.uuid(),scratchSchema:z.string()})),
 history:post('/workbench/history',session,z.object({runs:z.array(z.object({id:z.uuid(),kind:z.string(),status:z.string(),input:row,created_at:z.string(),duration_ms:z.number().nullable()}))})),
 draft:post('/workbench/draft',session.extend({model:z.string().optional(),sql:z.string().optional()}),z.object({model:z.string(),sql:z.string()})),
 query:post('/workbench/query',model.extend({cycleId:z.uuid().optional()}),AsyncStatus),
 previewModel:post('/workbench/previewModel',model.extend({cycleId:z.uuid().optional()}),AsyncStatus),
 backtest:post('/workbench/backtest',model.extend({cycleA:z.uuid(),cycleB:z.uuid(),keyColumns:z.array(z.string()).min(1)}),AsyncStatus),
 explain:post('/workbench/explain',model,ExplainResult),
 lineage:post('/workbench/lineage',model,ExplainResult),
 saveAsPr:post('/workbench/saveAsPr',model.extend({sql:z.string().min(1).max(100000),reviewToken:z.string().uuid().optional()}),z.object({branch:z.string(),diff:z.string(),pr_opened:z.boolean(),url:z.string().nullable(),message:z.string(),reviewToken:z.string().uuid().optional()})),
 status:post('/workbench/status',run,AsyncStatus),
 cancel:post('/workbench/cancel',run,AsyncStatus),
 result:post('/workbench/result',run,z.union([PreviewResult,BacktestResult]).nullable()),
 artifactContent:post('/workbench/artifactContent',z.object({artifactRef:z.string()}),z.unknown()),
 artifact:post('/workbench/artifact',z.object({artifactRef:z.string()}),z.object({url:z.string(),expiresIn:z.number()})),
 models:post('/workbench/models',session,z.object({models:z.array(z.string()),cycles:z.array(z.uuid()),cycleDetails:z.array(z.object({id:z.uuid(),cadence:z.string(),opened_at:z.string()})),sources:z.array(z.string())})),
};
export const rerunContract=post('/llm-steps/rerun',z.object({source_key:z.string(),config_version:z.string().regex(/^[a-f0-9]{64}$/),dbt_run_id:z.string(),input_relation:z.string()}),z.object({run_id:z.uuid(),status:z.string()}));
export const costsyncContract=post('/costs/sync',z.object({}),z.object({reconciled:z.number().int()}));
