import { describe, it, expect, vi, afterEach } from 'vitest';
import { inspectTargetCsv, targetCsv } from '../src/csv.js';
import { savePreview, readPreview } from '../src/import-preview.js';
afterEach(()=>vi.unstubAllEnvs());
describe('CSV import previews',()=>{
 it('retains valid CSV rows and reports physical line numbers for failures',()=>{
  const result=inspectTargetCsv('platform,handle\nfixture,fixture\nfixture,\nfixture,second\n');
  expect(result.rows).toHaveLength(2);
  expect(result.errors).toEqual([expect.stringContaining('Line 3:')]);
  expect(()=>targetCsv('platform,handle\nfixture,fixture\nfixture,')).toThrow('Line 3:');
  expect(inspectTargetCsv('platform,handle\nfixture,fixture,extra').errors[0]).toContain('Line 2:');
 });
 it('binds preserved previews to their actor and rejects tampered signatures',()=>{
  const token=savePreview('operator','platform,handle\nfixture,fixture','set');
  expect(readPreview(token,'operator').csv).toContain('fixture');
  expect(()=>readPreview(token,'other')).toThrow('expired');
  expect(()=>readPreview(token+'x','operator')).toThrow('expired');
 });

});
