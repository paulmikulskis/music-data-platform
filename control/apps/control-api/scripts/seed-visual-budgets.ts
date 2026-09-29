/** Explicit loopback-only demonstration data; never part of runtime provisioning. */
import { database } from '../src/db.js';
import postgres from 'postgres';
import { randomUUID } from 'node:crypto';

export async function seedVisualBudgets() {
  const url=new URL(process.env.MDP_CONTROL_RT_URL || '');
  if(!['127.0.0.1','localhost'].includes(url.hostname) || url.port!=='5433' || url.pathname!=='/control')
    throw new Error('Visual budget fixtures require the isolated loopback :5433/control database');
  const producerUrl=new URL(process.env.MDP_FUNCTIONS_TEST_URL || '');
  if(producerUrl.hostname!==url.hostname || producerUrl.port!==url.port || producerUrl.pathname!==url.pathname)
    throw new Error('Fixture cost producer must use the same isolated local database');
  const costs=postgres(producerUrl.toString(),{max:1});
  const db=database();
  try {
  const fixtures=[
    {scope:'global',name:'platform',cap:5000,spent:0,period:'monthly'},
    {scope:'streamline',name:'billboard_hot100',cap:1000,spent:35,period:'weekly'},
    {scope:'streamline',name:'mb_artist_catalog',cap:1000,spent:80,period:'monthly'},
    {scope:'llm_step',name:'local_fixture_creator_summary',cap:200,spent:225,period:'monthly'},
    {scope:'llm_step',name:'local_fixture_content_labels',cap:500,spent:20,period:'weekly'},
  ];
  for(const f of fixtures){
    let scopeId:string|null=null;
    if(f.scope==='llm_step'){
      const steps=await db.unsafe<{id:string}[]>(`INSERT INTO control.llm_step(source_key,model,params_hash,step_version,enabled)
        VALUES ($1,'local-fixture','local-visual-fixture','local-visual-fixture',false)
        ON CONFLICT (source_key,step_version) DO UPDATE SET enabled=false RETURNING id`,[f.name]);
      scopeId=steps[0]!.id;
    }else if(f.scope==='streamline'){
      const scopes=await db.unsafe<{id:string}[]>('SELECT id FROM control.streamline WHERE source_key=$1',[f.name]);
      if(!scopes[0])continue;
      scopeId=scopes[0].id;
    }
    const existing=await db.unsafe<{id:string}[]>('SELECT id FROM control.budget WHERE raised_by=$1',['local-visual-fixture:'+f.name]);
    const id=existing[0]?.id ?? randomUUID();
    await db.unsafe(`INSERT INTO control.budget(id,scope,scope_id,period,cap_cents,soft_pct,hard_action,ceiling_cents,raised_by)
      VALUES ($1,$2,$3,$4,$5,80,'warn',$6,$7) ON CONFLICT(id) DO NOTHING`,[id,f.scope,scopeId,f.period,f.cap,f.cap*2,'local-visual-fixture:'+f.name]);
    if(f.spent)await costs.unsafe(`INSERT INTO control.cost_ledger(streamline_id,llm_step_id,vendor,provider_request_id,unit,quantity,cost_cents,origin)
      VALUES ($1,$2,'local-visual-fixture',$3,'fixture',1,$4,'estimate')
      ON CONFLICT(vendor,provider_request_id,origin) DO UPDATE SET occurred_at=now()`,
      [f.scope==='streamline'?scopeId:null,f.scope==='llm_step'?scopeId:null,f.name,f.spent]);
  }
  // Consolidate settled, zero-cost acceptance placeholders without discarding their
  // run history or policy_snapshot. Active or nonzero reservations stay untouched.
  await costs.unsafe(`UPDATE control.budget_reservation r SET budget_id=(
    SELECT id FROM control.budget WHERE raised_by='local-visual-fixture:platform' LIMIT 1)
    FROM control.budget b WHERE r.budget_id=b.id AND r.settled_at IS NOT NULL
    AND r.reserved_cents=0 AND r.consumed_cents=0
    AND (b.period LIKE 'control-acceptance-%' OR b.period LIKE 'control-fixture-%')`);
  await db.unsafe(`DELETE FROM control.budget WHERE
    (period LIKE 'control-acceptance-%' OR period LIKE 'control-fixture-%')
    AND NOT EXISTS (SELECT 1 FROM control.budget_reservation r WHERE r.budget_id=budget.id)
    AND NOT EXISTS (SELECT 1 FROM control.llm_step s WHERE s.budget_id=budget.id)`);
  } finally { await costs.end(); }
}
