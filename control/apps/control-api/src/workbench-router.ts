import { STAFF_ANALYSIS_PATHS } from './auth.js';
import { implement, ORPCError } from '@orpc/server';
import { createHash, randomUUID } from 'node:crypto';
import { workbenchContract, rerunContract, costsyncContract } from '../../../packages/contracts/src/workbench.js';
import { z } from 'zod';
import type { Context } from './router.shared.js';
import { database, AppError, required } from './db.js';

const contracts={workbench:workbenchContract,rerun:rerunContract,sync:costsyncContract};
const impl=implement(contracts).$context<Context>().use(async({context,path,procedure,next})=>{
 const operation=procedure["~orpc"].route.path?.split("/").at(-1);
 if(!context.identity.admin && !(context.identity.staff && procedure["~orpc"].route.method === "POST" && STAFF_ANALYSIS_PATHS.has(procedure["~orpc"].route.path ?? ""))) {
  const error=new AppError('forbidden','This action needs the admin role. Ask an operator; open the access runbook.',403);
  throw new ORPCError('FORBIDDEN',{message:error.message,data:{error_class:error.error_class,next_step:error.next_step,runbook:error.runbook}});
 }
 if(['sandboxStatus','queries','history','models','draft','status','result','artifact','artifactContent','lineage','explain'].includes(String(operation))) return next();
 const id=randomUUID();
 await database().unsafe('INSERT INTO control.audit_log(id,actor,action,subject,after) VALUES ($1,$2,$3,$3,$4::text::jsonb)',[id,context.identity.actor,path.join('.'),JSON.stringify({state:'started'})]);
 try{const result=await next();await database().unsafe('UPDATE control.audit_log SET after=$2::text::jsonb WHERE id=$1',[id,JSON.stringify({state:'succeeded'})]);return result;}
 catch(e){await database().unsafe('UPDATE control.audit_log SET after=$2::text::jsonb WHERE id=$1',[id,JSON.stringify({state:'failed'})]);if(e instanceof AppError)throw new ORPCError('SERVICE',{status:e.status,message:e.message,data:{error_class:e.error_class,message:e.message}});throw e;}
});
async function forward<T extends z.ZodType>(action:string,schema:T|undefined,context:Context,input:object):Promise<z.output<T>>{
 const response=await fetch(new URL('/v1/workbench/'+action,required('MDP_WORKBENCH_URL')),{method:'POST',headers:{authorization:'Bearer '+(process.env.MDP_WORKBENCH_SERVICE_TOKEN ?? required('MDP_SERVICE_TOKEN')),'content-type':'application/json'},body:JSON.stringify({...input,userId:context.identity.admin ? context.identity.actor : `staff:${context.identity.actor}`,staff:!context.identity.admin,warehouseRole:context.identity.warehouse_role ?? null}),signal:AbortSignal.timeout(40000)});
 const body:unknown=await response.json();
 if(!response.ok){const e=z.object({error_class:z.string(),message:z.string()}).safeParse(body);throw new AppError(e.success?e.data.error_class:'workbench_failed',e.success?e.data.message:'Workbench request failed',response.status);}
 if(!schema)throw new Error('Missing workbench contract');
 return schema.parse(body);
}
// Ephemeral, one-use approval: a restart or expiry requires reviewing again.
const prReviews=new Map<string,{fingerprint:string;diff:string;expires:number}>();
const reviewFingerprint=(actor:string,input:{sessionId:string;model:string;sql:string})=>
 createHash('sha256').update(JSON.stringify([actor,input.sessionId,input.model,input.sql])).digest('hex');
async function saveAsPr(context:Context,input:{sessionId:string;model:string;sql:string;reviewToken?:string|undefined}){
 const schema=workbenchContract.saveAsPr['~orpc'].outputSchema;
 const {reviewToken,...draft}=input;
 const fingerprint=reviewFingerprint(context.identity.actor,draft);
 for(const [token,review] of prReviews)if(review.expires<=Date.now())prReviews.delete(token);
 if(reviewToken){
  const review=prReviews.get(reviewToken);
  if(!review || review.fingerprint!==fingerprint)throw new AppError('workbench_review_required','Review this exact SQL diff again before opening a pull request.',409);
  prReviews.delete(reviewToken);
  const current=await forward('saveAsPr',schema,context,{...draft,dryRun:true});
  if(current.diff!==review.diff)throw new AppError('workbench_review_changed','Repository files changed. Preview the updated diff before opening a pull request.',409);
  return forward('saveAsPr',schema,context,{...draft,dryRun:false});
 }
 const preview=await forward('saveAsPr',schema,context,{...draft,dryRun:true});
 const token=randomUUID();
 prReviews.set(token,{fingerprint,diff:preview.diff,expires:Date.now()+15*60*1000});
 return {...preview,reviewToken:token};
}
export const workbenchRouter={
 sandboxStatus:impl.workbench.sandboxStatus.handler(({context,input})=>forward('sandboxStatus',workbenchContract.sandboxStatus['~orpc'].outputSchema,context,input)),
 sandboxAction:impl.workbench.sandboxAction.handler(({context,input})=>forward('sandboxAction',workbenchContract.sandboxAction['~orpc'].outputSchema,context,input)),
 queries:impl.workbench.queries.handler(({context,input})=>forward('queries',workbenchContract.queries['~orpc'].outputSchema,context,input)),
 artifactContent:impl.workbench.artifactContent.handler(({context,input})=>forward('artifactContent',workbenchContract.artifactContent['~orpc'].outputSchema,context,input)),
 history:impl.workbench.history.handler(({context,input})=>forward('history',workbenchContract.history['~orpc'].outputSchema,context,input)),
 draft:impl.workbench.draft.handler(({context,input})=>forward('draft',workbenchContract.draft['~orpc'].outputSchema,context,input)),
 createSession:impl.workbench.createSession.handler(({context,input})=>forward('createSession',workbenchContract.createSession['~orpc'].outputSchema,context,input)),
 query:impl.workbench.query.handler(({context,input})=>forward('query',workbenchContract.query['~orpc'].outputSchema,context,input)),
 previewModel:impl.workbench.previewModel.handler(({context,input})=>forward('previewModel',workbenchContract.previewModel['~orpc'].outputSchema,context,input)),
 backtest:impl.workbench.backtest.handler(({context,input})=>forward('backtest',workbenchContract.backtest['~orpc'].outputSchema,context,input)),
 explain:impl.workbench.explain.handler(({context,input})=>forward('explain',workbenchContract.explain['~orpc'].outputSchema,context,input)),
 lineage:impl.workbench.lineage.handler(({context,input})=>forward('lineage',workbenchContract.lineage['~orpc'].outputSchema,context,input)),
 saveAsPr:impl.workbench.saveAsPr.handler(({context,input})=>saveAsPr(context,input)),
 status:impl.workbench.status.handler(({context,input})=>forward('status',workbenchContract.status['~orpc'].outputSchema,context,input)),
 cancel:impl.workbench.cancel.handler(({context,input})=>forward('cancel',workbenchContract.cancel['~orpc'].outputSchema,context,input)),
 result:impl.workbench.result.handler(({context,input})=>forward('result',workbenchContract.result['~orpc'].outputSchema,context,input)),
 artifact:impl.workbench.artifact.handler(({context,input})=>forward('artifact',workbenchContract.artifact['~orpc'].outputSchema,context,input)),
 models:impl.workbench.models.handler(({context,input})=>forward('models',workbenchContract.models['~orpc'].outputSchema,context,input)),
};
async function enrichment<T extends z.ZodType>(path:string,schema:T,body:unknown):Promise<z.output<T>>{
 const response=await fetch(new URL(path,process.env.MDP_ENRICHMENT_SERVICE_URL ?? required('MDP_SERVICE_URL')),{method:'POST',headers:{authorization:'Bearer '+(process.env.MDP_WORKBENCH_SERVICE_TOKEN ?? required('MDP_SERVICE_TOKEN')),'content-type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(40000)});
 const value:unknown=await response.json();if(!response.ok){const error=z.object({error_class:z.string(),message:z.string()}).parse(value);throw new AppError(error.error_class,error.message,response.status);}return schema.parse(value);
}
export const rerunProcedure=impl.rerun.handler(({input})=>enrichment('/v1/invoke',z.object({run_id:z.uuid(),status:z.string()}),input));
export const costsyncProcedure=impl.sync.handler(()=>enrichment('/v1/costsync',z.object({reconciled:z.number().int()}),{}));
