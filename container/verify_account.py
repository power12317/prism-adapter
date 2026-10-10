"""Opt-in real-account tool example via CPA; no arbitrary tool execution."""
import getpass
import json
import os
import uuid
from urllib.request import Request, ProxyHandler, build_opener

key = os.environ.get('CPA_API_KEY') or getpass.getpass('CPA API key: ')
url = os.environ.get('CPA_URL', 'http://127.0.0.1:8317/v1/responses')
headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
           'Session-Id': str(uuid.uuid4())}
payload = {'model': 'gpt-6.1-sol', 'reasoning': {'effort': 'xhigh'}, 'stream': True,
           'tools': [{'type': 'function', 'name': 'lookup',
                      'description': 'Return the fixture value. Call this first.',
                      'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
                     {'type': 'custom', 'name': 'echo',
                      'description': 'Confirm the fixture value obtained from lookup.',
                      'format': {'type': 'text'}}],
           'input': [{'role': 'user', 'content':
                      'Call lookup, then pass its returned value to echo, then report the confirmation. '
                      'Do not guess tool results. Use the tools in that order.'}]}
seen = []
for turn in range(6):
    req = Request(url, data=json.dumps(payload).encode(), headers=headers)
    with build_opener(ProxyHandler({})).open(req, timeout=600) as reply:
        events = [json.loads(line[6:]) for line in reply.read().decode().splitlines()
                  if line.startswith('data: ') and line[6:] != '[DONE]']
    completed = [e['response'] for e in events if e.get('type') == 'response.completed']
    if not completed:
        raise SystemExit('No response.completed event; inspect CPA logs.')
    response = completed[-1]
    print(json.dumps(response, ensure_ascii=False, indent=2))
    payload['input'] += response['output']
    calls = [item for item in response['output'] if item['type'] in ('function_call', 'custom_tool_call')]
    if not calls:
        if seen != ['function_call', 'custom_tool_call']:
            raise SystemExit(f'Model ended with unexpected tool sequence: {seen}')
        print('PASS: real account function -> custom -> final via CPA')
        break
    for call in calls:
        kind, name = call['type'], call['name']
        if kind == 'function_call' and name == 'lookup' and not seen:
            output = 'fixture-value'
        elif kind == 'custom_tool_call' and name == 'echo' and seen == ['function_call']:
            assert call['input'].strip() == 'fixture-value', 'Unexpected custom tool input'
            output = 'fixture-confirmed'
        else:
            raise SystemExit(f'Unexpected tool call: {kind}/{name}')
        seen.append(kind)
        payload['input'].append({'type': kind + '_output', 'call_id': call['call_id'], 'output': output})
else:
    raise SystemExit('Example stopped after six turns without a final message.')
