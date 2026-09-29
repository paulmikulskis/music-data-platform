import { describe,it,expect } from 'vitest';
import { Result } from '../src/pages.js';
import { Sparkline, RecentRuns } from '../src/run-components.js';
import { runDto } from '@mdp/contracts';
import { uiScript } from '../src/ui.js';
import { workbenchScript,rowLabel,bareModel } from '../src/workbench-visual.js';
import { readFileSync } from 'node:fs';

describe('page action and rendering boundaries',()=>{
 it('keeps alert acknowledgement distinct from starting a run even with a run id',async()=>{
  const html=await Result({result:{action:'alerts.acknowledgeGroup',result:{acknowledged:4,alert_class:'vendor_4xx',source_key:'fixture_accounts',run_id:'fixture-run'}}})!.toString();
  expect(html).toContain('Acknowledged 4 alerts · vendor_4xx · fixture_accounts');
  expect(html).not.toContain('Run started');expect(html).not.toContain('Open run, receipts and trace');
  expect(html).toContain('/ops?alert_class=vendor_4xx#alerts');
 });
 it('does not turn zero rejected string counts into warnings or graph a zero series',async()=>{
  const recorded=JSON.parse(readFileSync(new URL('../../../packages/contracts/test/fixtures/_v1_runs_get.json',import.meta.url),'utf8'));
  const source=Array.isArray(recorded.response)?recorded.response[0]:recorded.response.runs?.[0];
  const run=runDto.parse({...source,rows_written:'0',rows_rejected:'0',status:'succeeded'});
  const recent=await RecentRuns({runs:[run]}).toString();
  expect(recent).not.toContain('data-open-receipts');expect(recent).not.toContain('badge warning');expect(recent).not.toContain('rejected 0 ↗');
  expect(await Sparkline({runs:[run],mini:true}).toString()).toContain('No rows landed');
 });
 it('keeps progressive enhancement scripts valid after server interpolation',()=>{
  expect(()=>new Function(uiScript)).not.toThrow();expect(()=>new Function(workbenchScript)).not.toThrow();
 });
 it('formats known totals without inventing unavailable totals',()=>{
  expect(rowLabel(1)).toBe('1 row');expect(rowLabel(5000)).toBe('100 of 5,000 rows');
  expect(rowLabel(100,true)).toContain('total unavailable');expect(rowLabel(500,true)).toBe('First 100 rows · total unavailable');expect(rowLabel(100,true,5000)).toBe('100 of 5,000 rows');
  expect(bareModel('model.music_data_platform.mart_example')).toBe('mart_example');
 });
});
