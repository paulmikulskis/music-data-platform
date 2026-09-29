import {sandboxPolicy} from './sandbox-policy.generated.js';
import { z } from 'zod';
import { relationLabels } from './relation-labels.generated.js';

export const RelationLabel = z.object({
  layer: z.string(), category: z.string(), tenant: z.string(),
  learning: z.boolean(), resale: z.boolean(), licence_status: z.string(),
}).passthrough();
export const ResultLabel = RelationLabel.extend({
  tenants: z.array(z.string()), cross_tenant: z.boolean(), unresolved: z.boolean(),
});
export function labelsFor(schema: string, name: string): z.infer<typeof RelationLabel> {
  if (schema.startsWith(sandboxPolicy.prefix)) return RelationLabel.parse({layer:'sandbox',category:'personal',tenant:'unknown',learning:false,resale:false,licence_status:'unverified',derived_from:[]});
  if (schema.startsWith("explore_")) schema = schema.slice(8);
  const tenant = /^tenant_(.+)_(staging|intermediate|marts)$/.exec(schema);
  const family = tenant ? `tenant_*_${tenant[2]}` : schema === 'explore_raw' ? 'raw' : schema;
  const matches = Object.entries(relationLabels).filter(([key]) => key.split(".").at(-1) === name);
  const value = relationLabels[`${family}.${name}`] ?? (matches.length === 1 ? matches[0]?.[1] : undefined) ?? {
    layer: 'bronze', category: 'personal', tenant: 'unknown', learning: false,
    resale: false, licence_status: 'unverified',
  };
  return RelationLabel.parse(tenant ? {...value, tenant: tenant[1], learning: false} : value);
}
