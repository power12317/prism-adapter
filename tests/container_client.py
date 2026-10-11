"""Real HTTP JSON/SSE and function -> custom -> final, synthetic identities only."""
import json
from urllib.request import Request, ProxyHandler, build_opener

opener = build_opener(ProxyHandler({}))
headers = {'Content-Type': 'application/json', 'X-Prism-OAuth-Token': 'synthetic-token',
           'X-Prism-Account-ID': '300', 'X-Prism-Caller-ID': 'a' * 64,
           'X-Prism-Session-ID': 'b' * 64}
assert 'Authorization' not in headers


def request(payload):
    req = Request('http://127.0.0.1:8319/v1/responses',
                  data=json.dumps(payload).encode(), headers=headers)
    with opener.open(req, timeout=180) as reply:
        raw = reply.read().decode()
        if payload.get('stream'):
            assert reply.headers['Content-Type'] == 'text/event-stream'
            events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ')]
            assert events[-1]['type'] == 'response.completed'
            assert not any(event['type'].endswith('.delta') for event in events)
            result = events[-1]['response']
        else:
            result = json.loads(raw)
    assert result['model'] == 'gpt-6.1-sol' and result['usage'] is None
    return result


payload = {'model': 'gpt-6.1-sol', 'reasoning': {'effort': 'xhigh'},
           'input': [{'role': 'user', 'content': 'container-text-fixture'}]}
for stream in (False, True):
    payload['stream'] = stream
    result = request(payload)
    assert 'container-text-fixture' in result['output'][0]['content'][0]['text']

payload.update(stream=True, tools=[{'type': 'namespace', 'name': 'client', 'tools': [
    {'type': 'function', 'name': 'lookup', 'description': None, 'parameters': {'type': 'object',
     'properties': {'key': {'type': 'string'}}, 'required': ['key'], 'additionalProperties': False}},
    {'type': 'custom', 'name': 'echo', 'description': 'x' * 48753 + 'Z',
     'format': {'type': 'grammar', 'syntax': 'regex',
     'definition': 'fixture-value'}}]}])
payload['input'] = [{'role': 'user', 'content': 'Look up the fixture value, echo it, and report it.'}]
for turn, expected in enumerate(('function_call', 'custom_tool_call', 'message')):
    result = request(payload)
    item = result['output'][0]
    assert item['type'] == expected, item
    payload['input'] += result['output']
    if turn < 2:
        assert item['namespace'] == 'client'
        payload['input'].append({'type': expected + '_output', 'call_id': item['call_id'],
                                'output': 'fixture-value' if turn == 0 else 'fixture-confirmed'})
    else:
        assert item['content'][0]['text'] == 'fixture-confirmed'
print(json.dumps({'container_http': 'passed', 'model': 'gpt-6.1-sol', 'effort': 'xhigh',
                  'json': True, 'sse': True, 'tools': 'function -> custom -> final',
                  'long_tool_description_chars': 48754,
                  'bridge_authorization': False, 'real_upstream': False}))
