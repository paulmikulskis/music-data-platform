import { describe, expect, it } from 'vitest';
import { CompositionFrame } from '../src/workbench-page.js';

const column=(name:string,source:string|null)=>({name,source,type:'text',nullable:true});
const data={columns:[],rows:[{id:1}],compiledSql:'select 1',upstream:[],timingMs:1250,artifactRef:'fixture-artifact'};
const explanation={compiledSql:'select 1',upstream:[],downstream:[],sourceFreshness:[],producingRuns:[]};
const props={data,model:'marts.mart_report',cycle:'12345678-abcd',explanation,artifact:null};

describe('workbench composition frame',()=>{
 it('distinguishes tables from models, normalizes column sources and leaves joins and draft columns unweighted',async()=>{
  const html=await CompositionFrame({...props,data:{...data,
   upstream:['source.raw.accounts','model.project.enriched','marts.join_lookup'],
   columns:[column('id','accounts'),column('name','source.other.accounts'),column('score','marts.enriched'),column('draft',null)],
  },explanation:{...explanation,sourceFreshness:[{source_key:'accounts',last_published_at:'2026-09-18T08:00:00Z'}]}}).toString();
  // model name is now EntityName: slug-as-title + a copy chip for the qualified slug
  expect(html).toContain('<strong class="entity-title">mart_report</strong>');
  expect(html).toContain('data-copy-chip="marts.mart_report"');
  // source vs model distinguished by node class + glyph; hover popover carries the full id as title
  expect(html).toContain('class="node source"');
  expect(html).toContain('class="pop" tabindex="0" title="source.raw.accounts"');
  expect(html).toContain('<ellipse');
  expect(html).toContain('class="node model"');
  expect(html).toContain('class="pop" tabindex="0" title="model.project.enriched"');
  expect(html).toContain('<rect');
  // qualified prefix de-emphasised so the tail leads
  expect(html).toContain('<span class="qual-prefix">raw.</span>accounts');
  expect(html).toContain('2 cols');expect(html).toContain('1 cols');expect(html).toContain('join only');
  expect(html.match(/class="column-share"/g)).toHaveLength(2);
  expect(html).toContain('width:100%');expect(html).toContain('width:50%');
  expect(html).toMatch(/class="node source"[\s\S]*class="lineage-freshness published"/);
  expect(html.slice(0,html.indexOf('</figcaption>'))).not.toContain('lineage-freshness');
  // expandable output schema replaces the old text lineage disclosures
  expect(html).toContain('<details class="schema">');
  expect(html).toContain('Output schema · 4 columns');
  expect(html).not.toContain('Built from');expect(html).not.toContain('<script');
 });

 it('preserves runs, artifact and aggregate freshness even without declared inputs',async()=>{
  const html=await CompositionFrame({...props,artifact:{url:'/workbench/artifact?ref=fixture-artifact'},explanation:{...explanation,
   producingRuns:[{id:'run-1'},{id:'run-2'}],sourceFreshness:[{source_key:'unmatched',last_published_at:'2026-09-18T08:00:00Z'}],downstream:['marts.consumer'],
  }}).toString();
  expect(html).toContain('No declared upstream · defined in draft SQL');
  expect(html).not.toContain('class="nodes"');
  expect(html).toContain('<details class="lineage-runs"><summary>2 runs</summary>');
  expect(html).toContain('href="/runs/run-1"');expect(html).toContain('href="/runs/run-2"');
  expect(html).toContain('href="/workbench/artifact?ref=fixture-artifact" title="fixture-artifact"');
  expect(html).toContain('lineage-freshness published');
  expect(html).toContain('datetime="2026-09-18T08:00:00Z"');expect(html).toContain('feeds: consumer');
 });

 it('caps inputs at eight and exposes the remaining full identifiers without inventing timestamps',async()=>{
  const upstream=Array.from({length:10},(_,i)=>`source.raw.table_${i}`);
  const html=await CompositionFrame({...props,data:{...data,upstream},explanation:{...explanation,sourceFreshness:[{source_key:'table_0',last_published_at:null}]}}).toString();
  expect(html.match(/class="node source"/g)).toHaveLength(8);
  expect(html).toContain('+2 more');expect(html).toContain('title="source.raw.table_8\nsource.raw.table_9"');
  expect(html).toContain('lineage-freshness unknown');expect(html).toContain('No source timestamp');
  expect(html).not.toContain('column-share');expect(html).not.toContain('lineage-downstream');
  expect(html).not.toContain('artifact ↗');expect(html).not.toContain('<time');
 });
});
