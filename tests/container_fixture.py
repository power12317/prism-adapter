"""Test-only local upstream; production server, browser and HTTP handler unchanged."""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as api
from smoke_client_tools import ToolFixture


class CombinedFixture(ToolFixture):
    def reply(self, code, body, content_type='application/json', cache=False):
        if content_type == 'application/json':
            data = json.loads(body)
            if data.get('status') == 'completed':
                text = data['response']['payload']['output'][0]['content'][0]['text']
                if 'PRISM_CLIENT_TOOLS_V1:' not in text:
                    # Bypass only the tool fixture wrapper for ordinary text.
                    return super(ToolFixture, self).reply(code, body, content_type, cache)
        return super().reply(code, body, content_type, cache)


upstream = ThreadingHTTPServer(('127.0.0.1', 0), CombinedFixture)
upstream.daemon_threads = True
upstream.lock = threading.Lock()
upstream.asset_requests = 0
upstream.reconnect_first = False
upstream.reconnected = set()
upstream.jobs, upstream.release, upstream.mismatches, upstream.target = {}, None, 0, 1
threading.Thread(target=upstream.serve_forever, daemon=True).start()
api.BASE = f'http://127.0.0.1:{upstream.server_port}'
api.main()
