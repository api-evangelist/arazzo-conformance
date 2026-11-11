#!/usr/bin/env python3
"""Generate the execution cases: whole Arazzo 1.0.1 workflows a runner executes against scripts/mock-petstore.py.
Each case states the outcome (success | failure | any | contested: run and shown, never scored), the exact sequence of requests the mock must see, and
values those requests must carry (`match`: request index -> subset of {body, query, headers}).
Writes cases/exec/1.0.1/<id>.arazzo.yaml and cases/exec/1.0.1/manifest.yml."""
import os, yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'cases', 'exec', '1.0.1')
SPEC = 'https://spec.openapis.org/arazzo/v1.0.1.html'
OK = [{'condition': '$statusCode == 201'}]

def create(**kw):
    return {'stepId': 'create', 'operationId': 'createPet',
            'requestBody': {'contentType': 'application/json', 'payload': {'name': '$inputs.name'}},
            'successCriteria': OK, 'outputs': {'petId': '$response.body#/id'}} | kw

def fetch(pet='$steps.create.outputs.petId', **kw):
    return {'stepId': 'fetch', 'operationId': 'getPet', 'parameters': [{'name': 'petId', 'in': 'path', 'value': pet}],
            'successCriteria': [{'condition': '$statusCode == 200'}], 'outputs': {'status': '$response.body#/status'}} | kw

def listing(**kw):
    return {'stepId': 'list', 'operationId': 'listPets', 'successCriteria': [{'condition': '$statusCode == 200'}]} | kw

CONTESTED = ('Not scored. The spec example $[?count(@.pets) > 0] applies a filter to the root object itself; RFC 9535 applies a filter '
             'to the children of the root. A runner that follows one reading passes one of these two cases and fails the other.')

NAME = {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}

# (id, rule, spec anchor, workflows, inputs, expect, requests, match, note)
CASES = [
    ('chain-output-to-path', 'a step output feeds the next step\'s path parameter', 'runtime-expressions',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(), fetch()]}], {'name': 'Rex'},
     'success', ['POST /pets', 'GET /pets/pet-1'], {0: {'body': {'name': 'Rex'}}}, ''),
    ('payload-replacement', 'replacements overwrite the payload at a JSON Pointer before sending', 'payload-replacement-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(requestBody={'contentType': 'application/json', 'payload': {'name': '$inputs.name', 'status': 'available'},
                                                                         'replacements': [{'target': '/status', 'value': 'sold'}]})]}], {'name': 'Rex'},
     'success', ['POST /pets'], {0: {'body': {'name': 'Rex', 'status': 'sold'}}}, ''),
    ('query-parameter', 'a query parameter is sent on the request', 'parameter-object',
     [{'workflowId': 'run', 'steps': [listing(parameters=[{'name': 'limit', 'in': 'query', 'value': 2}])]}], {},
     'success', ['GET /pets'], {0: {'query': {'limit': '2'}}}, ''),
    ('header-parameter', 'a header parameter is sent on the request', 'parameter-object',
     [{'workflowId': 'run', 'steps': [listing(parameters=[{'name': 'X-Trace', 'in': 'header', 'value': 'conformance'}])]}], {},
     'success', ['GET /pets'], {0: {'headers': {'x-trace': 'conformance'}}}, ''),
    ('success-criteria-fail', 'an unmet successCriteria fails the step and the workflow, and nothing after it runs', 'step-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(successCriteria=[{'condition': '$statusCode == 200'}]), fetch()]}], {'name': 'Rex'},
     'failure', ['POST /pets'], {}, ''),
    ('body-condition-pass', 'a simple condition can compare a response body value', 'criterion-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(), fetch(successCriteria=[{'condition': '$statusCode == 200'}, {'condition': "$response.body#/status == 'available'"}])]}],
     {'name': 'Rex'}, 'success', ['POST /pets', 'GET /pets/pet-1'], {}, ''),
    ('body-condition-fail', 'a false body comparison fails the workflow', 'criterion-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(), fetch(successCriteria=[{'condition': '$statusCode == 200'}, {'condition': "$response.body#/status == 'sold'"}])]}],
     {'name': 'Rex'}, 'failure', ['POST /pets', 'GET /pets/pet-1'], {}, ''),
    ('regex-criterion-fail', 'a regex criterion that does not match fails the workflow', 'criterion-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(successCriteria=OK + [{'context': '$response.body#/name', 'condition': '^Max$', 'type': 'regex'}])]}],
     {'name': 'Rex'}, 'failure', ['POST /pets'], {}, ''),
    # Contested: the spec's own example ($[?count(@.pets) > 0] against an object body) filters the ROOT document;
    # RFC 9535 filters the root's CHILDREN. The two readings give opposite answers, so neither is scored.
    ('jsonpath-filter-over-object', 'a jsonpath filter over an object body, read the way the spec example reads', 'criterion-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(successCriteria=OK + [{'context': '$response.body', 'condition': "$[?(@.name == 'Rex')]", 'type': 'jsonpath'}])]}],
     {'name': 'Rex'}, 'contested', ['POST /pets'], {}, CONTESTED),
    ('jsonpath-filter-over-array', 'a jsonpath filter over an array body, read the way RFC 9535 reads', 'criterion-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(), listing(successCriteria=[{'condition': '$statusCode == 200'},
                                                                                       {'context': '$response.body', 'condition': "$[?(@.name == 'Rex')]", 'type': 'jsonpath'}])]}],
     {'name': 'Rex'}, 'contested', ['POST /pets', 'GET /pets'], {}, CONTESTED),
    ('on-success-end', 'onSuccess end stops the workflow after the step', 'success-action-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(onSuccess=[{'name': 'stop', 'type': 'end'}]), fetch()]}], {'name': 'Rex'},
     'success', ['POST /pets'], {}, ''),
    ('on-success-goto-skip', 'onSuccess goto jumps over the steps between', 'success-action-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(onSuccess=[{'name': 'jump', 'type': 'goto', 'stepId': 'fetch'}]), listing(), fetch()]}], {'name': 'Rex'},
     'success', ['POST /pets', 'GET /pets/pet-1'], {}, ''),
    ('on-failure-retry', 'onFailure retry repeats the step until it succeeds', 'failure-action-object',
     [{'workflowId': 'run', 'inputs': NAME, 'steps': [create(), fetch(onFailure=[{'name': 'again', 'type': 'retry', 'retryAfter': 0, 'retryLimit': 2,
                                                                                   'criteria': [{'condition': '$statusCode == 404'}]}])]}], {'name': 'flaky'},
     'success', ['POST /pets', 'GET /pets/pet-1', 'GET /pets/pet-1'], {}, 'The mock answers a pet named flaky with 404 once, then 200.'),
    ('retry-limit-exhausted', 'retryLimit 2 means two retries after the first attempt, then the step fails', 'failure-action-object',
     [{'workflowId': 'run', 'steps': [fetch(pet='missing', onFailure=[{'name': 'again', 'type': 'retry', 'retryAfter': 0, 'retryLimit': 2}])]}], {},
     'failure', ['GET /pets/missing'] * 3, {}, 'The spec: retryLimit is "how many attempts to retry the step may be attempted before failing", so 1 + 2 requests.'),
    ('on-failure-goto-recover', 'onFailure goto runs the recovery step', 'failure-action-object',
     [{'workflowId': 'run', 'steps': [fetch(pet='missing', onFailure=[{'name': 'recover', 'type': 'goto', 'stepId': 'list'}]), listing()]}], {},
     'any', ['GET /pets/missing', 'GET /pets'], {}, 'Whether a workflow that recovered counts as a success is not stated, so only the requests are judged.'),
    ('workflow-step', 'a step can run another workflow and use its outputs', 'step-object',
     [{'workflowId': 'adopt', 'inputs': NAME, 'steps': [create(), fetch()], 'outputs': {'petId': '$steps.create.outputs.petId'}},
      {'workflowId': 'run', 'steps': [{'stepId': 'sub', 'workflowId': 'adopt', 'parameters': [{'name': 'name', 'value': 'Rex'}],
                                       'outputs': {'petId': '$outputs.petId'}},
                                      fetch(pet='$steps.sub.outputs.petId', stepId='again')]}], {},
     'success', ['POST /pets', 'GET /pets/pet-1', 'GET /pets/pet-1'], {0: {'body': {'name': 'Rex'}}}, ''),
]

def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith('.arazzo.yaml'): os.remove(os.path.join(OUT, f))
    manifest = []
    for cid, rule, anchor, workflows, inputs, expect, requests, match, note in CASES:
        d = {'arazzo': '1.0.1', 'info': {'title': f'{cid}: {rule}', 'version': '1.0.0'},
             'sourceDescriptions': [{'name': 'petstore', 'url': '../../ae/sources/petstore.openapi.yaml', 'type': 'openapi'}],
             'workflows': workflows}
        fn = f'{cid}.arazzo.yaml'
        with open(os.path.join(OUT, fn), 'w') as fh:
            yaml.safe_dump(d, fh, sort_keys=False, allow_unicode=True, width=140)
        manifest.append({'id': f'exec-{cid}', 'file': fn, 'version': '1.0.1', 'workflow': 'run', 'inputs': inputs, 'expect': expect,
                         'requests': requests, 'match': match, 'rule': rule, 'spec': f'{SPEC}#{anchor}'} | ({'note': note} if note else {}))
    with open(os.path.join(OUT, 'manifest.yml'), 'w') as fh:
        yaml.safe_dump({'suite': 'exec', 'cases': manifest}, fh, sort_keys=False, allow_unicode=True, width=160)
    print(len(manifest), 'cases ->', os.path.relpath(OUT))

if __name__ == '__main__':
    main()
