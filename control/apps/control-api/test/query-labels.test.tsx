import { describe, expect, it } from 'vitest';
import { labelsFor, ResultLabel } from '@mdp/data-sdk';
import { LabelChips } from '../src/label-chips.js';
import { PreviewResult } from '../../../packages/contracts/src/workbench.js';

describe('query labels', () => {
  it('keeps tenant identity and conservative rights on served metadata', () => {
    const label = labelsFor('tenant_two-co_marts', 'mart_creator_directory');
    expect(label.tenant).toBe('two-co');
    expect(label.learning).toBe(false);
    expect(labelsFor('unknown', 'not_registered').resale).toBe(false);
  });
  it('renders a red cross-tenant warning and preserves labels in result contracts', () => {
    const labels = ResultLabel.parse({...labelsFor('marts', 'mart_chart_history'), tenants:['one','two'], cross_tenant:true, unresolved:false});
    const html = String(LabelChips({labels}));
    expect(html).toContain('Mixes tenant data:');
    expect(html).toContain('var(--danger)');
    expect(html).toContain('Review the tenant labels before export.');
    const value = PreviewResult.parse({labels, columns:[], rows:[], compiledSql:'select 1', upstream:[], timingMs:1, artifactRef:'local'});
    expect(value.labels?.cross_tenant).toBe(true);
  });
});

it('shows global scope for a global explore copy', () => {
  const labels = ResultLabel.parse({
    ...labelsFor('explore_staging', 'stg_billboard__chart_entries'),
    category: 'vendor-licensed',
    tenants: [],
    cross_tenant: false,
    unresolved: false,
  });
  const html = String(LabelChips({ labels }));
  expect(html).toContain('>global<');
  expect(html).not.toContain('tenant:');
  expect(html).not.toContain('personal');
  expect(html).not.toContain('label-note');
});

it('names one tenant without an unresolved warning', () => {
  const labels = ResultLabel.parse({
    ...labelsFor('tenant_fixture_marts', 'mart_creator_directory'),
    tenants: ['fixture'],
    cross_tenant: false,
    unresolved: false,
  });
  const html = String(LabelChips({ labels }));
  expect(html).toContain('tenant: fixture');
  expect(html).not.toContain('role="alert"');
});

it('names an unresolved input with a plain link to review it', () => {
  const labels = ResultLabel.parse({
    ...labelsFor('staging', 'missing_input'),
    tenants: [],
    cross_tenant: false,
    unresolved: true,
    unresolved_inputs: ['staging.missing_input'],
  });
  const html = String(LabelChips({ labels }));
  expect(html).toContain('Labels are unknown for staging.missing_input.');
  expect(html).toContain('/explorer?q=staging.missing_input');
  expect(html).toContain('Review input labels');
  expect(html).not.toContain('<strong');
  expect(html).not.toContain('var(--danger)');
});
