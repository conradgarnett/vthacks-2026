import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { jsonSchemas } from '@sense/protocol';

const outDir = join(import.meta.dirname, '..', 'docs', 'schemas');
mkdirSync(outDir, { recursive: true });
for (const [name, schema] of Object.entries(jsonSchemas())) {
  writeFileSync(join(outDir, `${name}.schema.json`), JSON.stringify(schema, null, 2) + '\n');
}
console.log(`wrote ${Object.keys(jsonSchemas()).length} JSON Schemas to docs/schemas/`);
