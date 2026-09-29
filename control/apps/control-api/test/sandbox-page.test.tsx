import {describe,it,expect} from 'vitest';
import {SandboxPage} from '../src/sandbox-page.js';
import {relationLabels,sandboxFacts} from '../src/console-semantics.js';
const row={schema_name:'sandbox_demo',owner_role:'analyst_demo',is_explorer:false,sharing:'analysts and explorers',size_bytes:1048576,quota_bytes:1073741824,login_enabled:true,object_count:1,objects:[{name:'chart_copy',kind:'r',bytes:1048576,labels:JSON.stringify({derived_from:['marts.chart']})}],dependents:[{relation:'sandbox_peer.draft',owner:'analyst_peer'}],notices:[]};
describe('sandbox console',()=>{
 it('shows ownership, space, origins, dependents and useful next steps',async()=>{
  const html=await SandboxPage({rows:[row],selected:'sandbox_demo',inputs:[{schema:'marts',name:'chart'}]}).toString();
  for(const text of ['Sandbox','analyst_demo','marts.chart','analyst_peer','Copy source SELECT','Copy status command','Login'])expect(html).toContain(text);
  expect(html).not.toContain('Copy rotate command');
 });
 it('gives operators concrete access and archive actions',async()=>{
  const html=await SandboxPage({rows:[row],operator:true}).toString();
  for(const text of ['Freeze new objects','Disable login','Copy rotate command','Copy archive command','archive.json','--transfer'])expect(html).toContain(text);
 });
 it('describes missing and frozen sandboxes without a dead end',async()=>{
  expect(await SandboxPage({rows:[]}).toString()).toContain('analyst-add.sh');
  expect(sandboxFacts({...row,quota_frozen:true}).state).toBe('frozen');
  expect(relationLabels('sandbox_demo.chart_copy').layer).toBe('sandbox');
 });
});
