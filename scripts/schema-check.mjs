// Reference baseline: validate one document against the official OAI JSON Schema for its version.
// Usage: node schema-check.mjs <schema.yaml> <doc.yaml>. Exit 0 valid, 1 invalid.
import fs from 'node:fs';
import { load } from 'js-yaml';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const [schemaPath, docPath] = process.argv.slice(2);
const schema = load(fs.readFileSync(schemaPath, 'utf8'));
const doc = load(fs.readFileSync(docPath, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validate = ajv.compile(schema);
if (validate(doc)) { console.log('valid'); process.exit(0); }
for (const e of validate.errors.slice(0, 10)) console.log(`${e.instancePath || '/'} ${e.message}`);
process.exit(1);
