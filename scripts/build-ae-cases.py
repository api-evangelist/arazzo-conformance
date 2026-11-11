#!/usr/bin/env python3
"""Generate the API Evangelist Arazzo 1.0.1 cases from one valid base document plus one mutation per case.
Each case breaks (or exercises) exactly ONE rule, and names the spec section that states it.
Writes cases/ae/1.0.1/<id>.arazzo.yaml and cases/ae/1.0.1/manifest.yml. Re-run after editing CASES."""
import copy, os, yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'cases', 'ae', '1.0.1')
SPEC = 'https://spec.openapis.org/arazzo/v1.0.1.html'

BASE = {
    'arazzo': '1.0.1',
    'info': {'title': 'Conformance base', 'version': '1.0.0'},
    'sourceDescriptions': [{'name': 'petstore', 'url': '../sources/petstore.openapi.yaml', 'type': 'openapi'}],
    'workflows': [{
        'workflowId': 'adoptPet',
        'inputs': {'type': 'object', 'properties': {'name': {'type': 'string'}}},
        'steps': [
            {'stepId': 'create', 'operationId': 'createPet',
             'requestBody': {'contentType': 'application/json', 'payload': {'name': '$inputs.name'}},
             'successCriteria': [{'condition': '$statusCode == 201'}],
             'outputs': {'petId': '$response.body#/id'}},
            {'stepId': 'fetch', 'operationId': 'getPet',
             'parameters': [{'name': 'petId', 'in': 'path', 'value': '$steps.create.outputs.petId'}],
             'successCriteria': [{'condition': '$statusCode == 200'}],
             'outputs': {'status': '$response.body#/status'}},
        ],
        'outputs': {'petId': '$steps.create.outputs.petId'},
    }],
}

# Where the official schema and the spec's prose disagree, the prose wins and the case says so.
NOTES = {
    'valid-criterion-expression-type': 'The official 1.0 schema rejects this: its anyOf applies criterion-expression-type-object to the '
                                       'Criterion Object itself instead of to its type property, so the documented object form cannot pass.',
    'invalid-output-key-pattern': 'The spec states the pattern; the official 1.0 schema does not enforce it on step outputs.',
}

def wf(d, i=0): return d['workflows'][i]
def step(d, i=0, w=0): return d['workflows'][w]['steps'][i]

# (id, expect, level, rule, spec anchor, mutate)
# level: schema = the official JSON Schema alone decides it; semantic = only a validator that reads the spec's prose catches it
CASES = [
    # ---- valid documents that use a feature a tool might not know
    ('valid-base', 'valid', 'schema', 'the base document: two steps, request body, path parameter, runtime expressions', 'arazzo-description', lambda d: None),
    ('valid-operation-path', 'valid', 'schema', 'a step may target an operation by operationPath (JSON Pointer into the source)', 'step-object',
     lambda d: step(d, 1).update(operationPath="{$sourceDescriptions.petstore.url}#/paths/~1pets~1{petId}/get") or step(d, 1).pop('operationId')),
    ('valid-source-qualified-operation-id', 'valid', 'semantic', 'an operationId may be qualified with $sourceDescriptions.<name>.', 'step-object',
     lambda d: step(d, 0).update(operationId='$sourceDescriptions.petstore.createPet')),
    ('valid-workflow-step', 'valid', 'semantic', 'a step may invoke another workflow in the same document by workflowId', 'step-object',
     lambda d: d['workflows'].append({'workflowId': 'adoptTwice', 'steps': [{'stepId': 'first', 'workflowId': 'adoptPet', 'parameters': [{'name': 'name', 'value': 'Rex'}]}]})),
    ('valid-depends-on', 'valid', 'semantic', 'dependsOn names another workflow that exists', 'workflow-object',
     lambda d: d['workflows'].append({'workflowId': 'listAfter', 'dependsOn': ['adoptPet'], 'steps': [{'stepId': 'list', 'operationId': 'listPets'}]})),
    ('valid-on-success-goto', 'valid', 'semantic', 'onSuccess goto a later stepId', 'success-action-object',
     lambda d: step(d, 0).update(onSuccess=[{'name': 'skipAhead', 'type': 'goto', 'stepId': 'fetch'}])),
    ('valid-on-failure-retry', 'valid', 'schema', 'onFailure retry with retryAfter and retryLimit', 'failure-action-object',
     lambda d: step(d, 1).update(onFailure=[{'name': 'retryFetch', 'type': 'retry', 'retryAfter': 1, 'retryLimit': 3, 'criteria': [{'condition': '$statusCode == 404'}]}])),
    ('valid-criteria-regex-jsonpath', 'valid', 'schema', 'criterion types regex and jsonpath with a context', 'criterion-object',
     lambda d: step(d, 1)['successCriteria'].extend([{'context': '$response.body#/name', 'condition': '^[A-Za-z]+$', 'type': 'regex'},
                                                       {'context': '$response.body', 'condition': '$[?(@.name)]', 'type': 'jsonpath'}])),
    ('valid-criterion-expression-type', 'valid', 'schema', 'criterion type given as a Criterion Expression Type Object', 'criterion-expression-type-object',
     lambda d: step(d, 1)['successCriteria'].append({'context': '$response.body', 'condition': '$.name', 'type': {'type': 'jsonpath', 'version': 'draft-goessner-dispatch-jsonpath-00'}})),
    ('valid-components-reuse', 'valid', 'semantic', 'a step parameter reused from components via a Reusable Object', 'reusable-object',
     lambda d: (d.update(components={'parameters': {'limit': {'name': 'limit', 'in': 'query', 'value': 10}}}),
                wf(d).setdefault('steps', []).append({'stepId': 'list', 'operationId': 'listPets', 'parameters': [{'reference': '$components.parameters.limit'}]}))),
    ('valid-payload-replacements', 'valid', 'schema', 'requestBody replacements target the payload with a JSON Pointer', 'payload-replacement-object',
     lambda d: step(d, 0)['requestBody'].update(replacements=[{'target': '/status', 'value': 'available'}])),
    ('valid-specification-extensions', 'valid', 'schema', 'x- extensions are allowed on every object', 'specification-extensions',
     lambda d: (d.update({'x-owner': 'ae'}), wf(d).update({'x-tier': 1}), step(d).update({'x-note': 'n'}))),
    ('valid-arazzo-1-0-0', 'valid', 'schema', 'a 1.0.0 document is still a valid Arazzo 1.0.x document', 'versions',
     lambda d: d.update(arazzo='1.0.0')),

    # ---- invalid: the JSON Schema rejects these
    ('invalid-missing-info', 'invalid', 'schema', 'info is REQUIRED', 'arazzo-description', lambda d: d.pop('info')),
    ('invalid-missing-source-descriptions', 'invalid', 'schema', 'sourceDescriptions is REQUIRED and has at least one entry', 'arazzo-description',
     lambda d: d.update(sourceDescriptions=[])),
    ('invalid-missing-steps', 'invalid', 'schema', 'a workflow REQUIRES at least one step', 'workflow-object', lambda d: wf(d).update(steps=[])),
    ('invalid-step-two-targets', 'invalid', 'schema', 'operationId, operationPath and workflowId are mutually exclusive', 'step-object',
     lambda d: step(d, 0).update(workflowId='adoptPet')),
    ('invalid-step-no-target', 'invalid', 'schema', 'a step MUST name one of operationId, operationPath or workflowId', 'step-object',
     lambda d: step(d, 0).pop('operationId')),
    ('invalid-output-key-pattern', 'invalid', 'semantic', 'output keys MUST match ^[a-zA-Z0-9\\.\\-_]+$', 'step-object',
     lambda d: step(d, 0).update(outputs={'pet id': '$response.body#/id'})),
    ('invalid-parameter-in-value', 'invalid', 'schema', 'parameter in MUST be path, query, header or cookie', 'parameter-object',
     lambda d: step(d, 1)['parameters'][0].update({'in': 'body'})),
    ('invalid-action-type', 'invalid', 'schema', 'success action type MUST be end or goto', 'success-action-object',
     lambda d: step(d, 0).update(onSuccess=[{'name': 'again', 'type': 'retry'}])),
    ('invalid-arazzo-version', 'invalid', 'schema', 'arazzo MUST be a 1.0.x version string', 'fixed-fields',
     lambda d: d.update(arazzo='2.0.0')),

    # ---- invalid: only the prose of the spec rules these out
    ('invalid-duplicate-step-id', 'invalid', 'semantic', 'stepId MUST be unique among the steps of a workflow', 'step-object',
     lambda d: step(d, 1).update(stepId='create')),
    ('invalid-duplicate-workflow-id', 'invalid', 'semantic', 'workflowId MUST be unique among the workflows of a description', 'workflow-object',
     lambda d: d['workflows'].append(copy.deepcopy(wf(d)))),
    ('invalid-unresolved-operation-id', 'invalid', 'semantic', 'operationId MUST resolve to an operation in a source description', 'step-object',
     lambda d: step(d, 0).update(operationId='adoptPetNow')),
    ('invalid-unknown-source-qualifier', 'invalid', 'semantic', 'a $sourceDescriptions.<name> qualifier MUST name a declared source', 'step-object',
     lambda d: step(d, 0).update(operationId='$sourceDescriptions.zoo.createPet')),
    ('invalid-goto-unknown-step', 'invalid', 'semantic', 'goto stepId MUST name a step in the current workflow', 'success-action-object',
     lambda d: step(d, 0).update(onSuccess=[{'name': 'jump', 'type': 'goto', 'stepId': 'nowhere'}])),
    ('invalid-goto-both-targets', 'invalid', 'semantic', 'a goto names stepId OR workflowId, not both', 'success-action-object',
     lambda d: step(d, 0).update(onSuccess=[{'name': 'jump', 'type': 'goto', 'stepId': 'fetch', 'workflowId': 'adoptPet'}])),
    ('invalid-depends-on-unknown', 'invalid', 'semantic', 'dependsOn MUST name workflows that exist', 'workflow-object',
     lambda d: wf(d).update(dependsOn=['ghostWorkflow'])),
    ('invalid-step-workflow-unknown', 'invalid', 'semantic', 'a step workflowId MUST name a workflow that exists', 'step-object',
     lambda d: d['workflows'].append({'workflowId': 'wrapper', 'steps': [{'stepId': 's', 'workflowId': 'ghostWorkflow'}]})),
    ('invalid-duplicate-parameter', 'invalid', 'semantic', 'a parameter is unique by name and in; the list MUST NOT duplicate one', 'step-object',
     lambda d: step(d, 1)['parameters'].append({'name': 'petId', 'in': 'path', 'value': 'x'})),
    ('invalid-operation-parameter-without-in', 'invalid', 'semantic', 'a parameter on an operation step MUST specify in', 'parameter-object',
     lambda d: step(d, 1)['parameters'][0].pop('in')),
    ('invalid-runtime-expression', 'invalid', 'semantic', 'runtime expressions MUST follow the ABNF ($resposne is not a source)', 'runtime-expressions',
     lambda d: step(d, 0).update(outputs={'petId': '$resposne.body#/id'})),
    ('invalid-step-output-reference', 'invalid', 'semantic', '$steps.<id> MUST name a step that exists', 'runtime-expressions',
     lambda d: step(d, 1)['parameters'][0].update(value='$steps.missing.outputs.petId')),
    ('invalid-unresolved-component', 'invalid', 'semantic', 'a Reusable Object reference MUST resolve to a component', 'reusable-object',
     lambda d: step(d, 1)['parameters'].append({'reference': '$components.parameters.missing'})),
    ('invalid-criterion-type-without-context', 'invalid', 'schema', 'context MUST be provided when a criterion type is regex/jsonpath/xpath', 'criterion-object',
     lambda d: step(d, 1)['successCriteria'].append({'condition': '^ok$', 'type': 'regex'})),
    ('invalid-retry-after-negative', 'invalid', 'schema', 'retryAfter MUST be a non-negative number', 'failure-action-object',
     lambda d: step(d, 1).update(onFailure=[{'name': 'r', 'type': 'retry', 'retryAfter': -1}])),
    ('invalid-workflow-step-with-request-body', 'invalid', 'semantic', 'requestBody is only for steps that target an operation, not a workflow', 'step-object',
     lambda d: d['workflows'].append({'workflowId': 'wrapper', 'steps': [{'stepId': 's', 'workflowId': 'adoptPet', 'requestBody': {'payload': {'name': 'x'}}}]})),
]

def main():
    for f in os.listdir(OUT):
        if f.endswith('.arazzo.yaml'): os.remove(os.path.join(OUT, f))
    manifest = []
    for cid, expect, level, rule, anchor, mutate in CASES:
        d = copy.deepcopy(BASE)
        mutate(d)
        if 'info' in d: d['info']['title'] = f'{cid}: {rule}'[:200]
        fn = f'{cid}.arazzo.yaml'
        with open(os.path.join(OUT, fn), 'w') as fh:
            yaml.safe_dump(d, fh, sort_keys=False, allow_unicode=True, width=140)
        manifest.append({'id': cid, 'file': fn, 'version': '1.0.1', 'expect': expect, 'level': level, 'rule': rule, 'spec': f'{SPEC}#{anchor}'}
                        | ({'note': NOTES[cid]} if cid in NOTES else {}))
    with open(os.path.join(OUT, 'manifest.yml'), 'w') as fh:
        yaml.safe_dump({'suite': 'ae', 'cases': manifest}, fh, sort_keys=False, allow_unicode=True, width=160)
    print(len(manifest), 'cases ->', os.path.relpath(OUT))

if __name__ == '__main__':
    main()
