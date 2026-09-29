import { OpenAPIGenerator } from '@orpc/openapi';
import { ZodToJsonSchemaConverter } from '@orpc/zod/zod4';
import { readFileSync } from 'node:fs';
import { expect, test } from 'vitest';
import { router } from '../../../apps/control-api/src/router.js';
import { router as dataRouter } from '../../../apps/data-api/src/generated/router.js';

for (const [name, title, implementation] of [
  ['control-api', 'MDP Control API', router],
  ['data-api', 'MDP Data API', dataRouter],
] as const) {
  test(`committed ${name} OpenAPI matches the implementation router`, async () => {
    const document = await new OpenAPIGenerator({
      schemaConverters: [new ZodToJsonSchemaConverter()],
    }).generate(implementation, {
      info: { title, version: '1.0.0' },
      servers: [{ url: '/api' }],
    });
    expect(JSON.stringify(document, null, 2) + '\n').toBe(
      readFileSync(new URL(`../openapi/${name}.json`, import.meta.url), 'utf8'),
    );
  });
}
