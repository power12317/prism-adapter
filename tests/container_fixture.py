"""Test-only local upstream; production server, browser and HTTP handler unchanged."""
import json
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as api
from smoke_browser import FixtureHandler


class CombinedFixture(FixtureHandler):
    def send(self, data, content_type='application/json'):
        if content_type == 'application/json':
            payload = json.loads(data)
            if payload.get('status') == 'completed':
                item = payload['response']['payload']['output'][0]['content'][0]
                prompt = item['text']
                marker = re.search(r'PRISM_CLIENT_TOOLS_V1:[a-f0-9]+', prompt)
                if marker:
                    if 'custom_tool_call_output' in prompt:
                        result = {'kind': 'final', 'text': 'fixture-confirmed'}
                    elif 'function_call_output' in prompt:
                        result = {'kind': 'calls', 'calls': [{'name': 'client.echo', 'input': 'fixture-value'}]}
                    else:
                        result = {'kind': 'calls', 'calls': [{'name': 'client.lookup', 'arguments': {'key': 'fixture'}}]}
                    item['text'] = marker.group() + '\n' + json.dumps(result)
                    data = json.dumps(payload)
        super().send(data, content_type)


def main():
    # The browser-mode fixture authors its own New/Blank project and polling UI.
    # The separate original multiplex fixture remains covered by acceptance.sh.
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), CombinedFixture)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    api.BASE = f'http://127.0.0.1:{upstream.server_port}'
    api.main()


if __name__ == '__main__':
    main()
