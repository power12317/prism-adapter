"""Process liveness only; does not launch Chromium or contact Prism."""
import json
import os
from urllib.request import ProxyHandler, build_opener

port = int(os.environ.get("PRISM_ADAPTER_PORT", "8319"))
with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/health", timeout=3) as reply:
    assert reply.status == 200 and json.load(reply)["status"] == "ok"
