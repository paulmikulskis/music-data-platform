import { expect, it } from 'vitest';
import { errorCatalog, errorHint } from '@mdp/contracts';
import { RefusalPopover, DetailPopover, EmptyState, Notice, EntityPopover } from '../src/primitives.js';
import { Result } from '../src/pages.js';

for (const code of Object.keys(errorCatalog)) {
  it(`${code} offers recovery links and plain guidance`, async () => {
    const html = String(await RefusalPopover({code, message:'<script>failure</script>'}));
    expect(html).toMatch(/<a href="[^"]+"/);
    expect(html).toContain(errorHint(code).next_step.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll("'", '&#39;').replaceAll('"', '&quot;'));
    expect(html).not.toContain('<script>failure');
  });
}
it('unknown codes retain the message and real trace and audit links', async () => {
  const html = String(await RefusalPopover({code:'future_code',message:'Exact cause',traceId:'trace-a',auditId:'audit-a'}));
  expect(html).toContain('Exact cause');
  expect(html).toContain('/traces/trace-a');
  expect(html).toContain('/audit/audit-a');
  expect(errorHint('__proto__')).toEqual(errorCatalog.unmapped);
});
it('a failed generic action without any ids still offers a next step', async () => {
  const html = String(await Result({result:{state:'failed',message:'Exact cause',error_class:'scope_mismatch'}}));
  expect(html).toContain('Exact cause');
  expect(html).toContain('Next step:');
  expect(html).toContain('/ops');
});
it('a retry carries the original complete form fields', async () => {
  const html = String(await Result({result:{state:'failed',action:'dbt.retry',input:{cycle_id:'cycle-a'},error_class:'service_unreachable'}}));
  expect(html).toContain('/actions/retry');
  expect(html).toContain('name="cycle_id" value="cycle-a"');
});
it('does not reconstruct missing or redacted retry fields', async () => {
  const html=String(await Result({result:{state:'failed',action:'dbt.retry',input:{cycle_id:'[redacted]'}}}));
  expect(html).not.toContain('/actions/retry');
  expect(html).toContain('Next step:');
});
it('requires recovery actions at primitive boundaries', () => {
  if (false) {
    // @ts-expect-error Empty states require an action.
    EmptyState({message:'Empty'});
    // @ts-expect-error Error, toast and banner notices require an action.
    Notice({children:'Failed',tone:'error'});
    // @ts-expect-error Popovers require an action.
    DetailPopover({label:'Details',children:'Details'});
    // @ts-expect-error Entity popovers require at least one link.
    EntityPopover({label:'Table',summary:'Table',facts:{links:[]}});
  }
});
