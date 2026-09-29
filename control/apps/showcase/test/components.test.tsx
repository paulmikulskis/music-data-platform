import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Number, type Provenance } from '../components/number';
import { Status } from '../components/status';
const p: Provenance = { queried_at: '2026-09-25T12:00:00Z', scope: 'global', provenance: 'live query', query: 'Count scheduled cycles.', sql: 'SELECT count(*) FROM control.cycle;' };
describe('provenance', () => {
  it('does not turn a missing stamp into live provenance', () => {
    expect(renderToStaticMarkup(<Number label="Rows" value="0" />)).toContain('provenance unknown');
    expect(renderToStaticMarkup(<Number label="Rows" value="0" provenance={{ ...p, provenance: undefined, build: { relation: 'marts.example', scope: 'global', tenant_slug: null, stamped: false, cycle_id: null, close_no: null, built_at: null } }} />)).toContain('provenance unknown');
  });
  it('shows real read time, scoped build and exact large close numbers', () => {
    const html = renderToStaticMarkup(<Number label="Rows" value="1" provenance={{ ...p, cadence: 'daily', build: { relation: 'tenant_quartz_marts.example', scope: 'tenant', tenant_slug: 'quartz', stamped: true, cycle_id: 'cycle', close_no: '9007199254740993', built_at: '2026-09-25T11:00:00Z' } }} />);
    expect(html).toContain('9007199254740993'); expect(html).toContain('tenant:quartz'); expect(html).toContain(p.queried_at); expect(html).toContain('Copy SQL');
  });
  it('shows counts below twenty and the denominator for percentages', () => {
    const low = renderToStaticMarkup(<Number label="Coverage" value={null} provenance={p} percentage={{ count: 9, n: 19 }} />);
    expect(low).toContain('9 of 19'); expect(low).not.toContain('%');
    const high = renderToStaticMarkup(<Number label="Coverage" value={null} provenance={p} percentage={{ count: 10, n: 20 }} />);
    expect(high).toContain('50.0%'); expect(high).toContain('20');
  });
});
it('Coming includes a target and has a separate visual state', () => {
  expect(renderToStaticMarkup(<Status state="Coming" target="Next release" />)).toContain('Coming');
  expect(renderToStaticMarkup(<Status state="Coming" target="Next release" />)).toContain('Next release');
});
