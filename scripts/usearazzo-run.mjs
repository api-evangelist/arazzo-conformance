// CLI shim for @usearazzo/runner, which ships as a library only.
// Usage: node usearazzo-run.mjs <file> <workflowId> '<inputs json>'. Prints the run status; exit 1 only when 'failed'
// (completed, ended by an end action, and transferred by a goto to another workflow all finish the workflow).
import path from 'node:path';
import { ArazzoRunner } from '@usearazzo/runner';

const [file, workflowId, inputs = '{}'] = process.argv.slice(2);
const runner = await ArazzoRunner.load(path.resolve(file));
const result = await runner.execute(workflowId, { inputs: JSON.parse(inputs) });
for (const s of result.steps ?? []) console.log(`${s.successful ? 'ok' : 'failed'} ${s.stepId}`);
console.log(`status: ${result.status}`);
process.exit(result.status === 'failed' ? 1 : 0);
