#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PRISM_ADAPTER_IMAGE=${1:?Pass the built image reference}
repo=$PWD
run=$(mktemp -d)
export CI_STATE_DIR=$run/prism-data
export CLI_PROXY_CONFIG_PATH=$run/config.yaml
export CLI_PROXY_AUTH_PATH=$run/auths
export CLI_PROXY_LOG_PATH=$run/logs
export CLI_PROXY_PLUGIN_PATH=$run/plugins
mkdir -p "$CLI_PROXY_AUTH_PATH" "$CLI_PROXY_LOG_PATH" "$CLI_PROXY_PLUGIN_PATH"
cat > "$CLI_PROXY_CONFIG_PATH" <<'YAML'
host: ""
port: 8317
auth-dir: /root/.cli-proxy-api
api-keys: [fixture-cpa-key]
YAML
dc=(docker compose --project-directory "$repo" -p prism-ci -f docker-compose.prism.yml -f tests/compose.ci.yml)
cleanup() {
  "${dc[@]}" logs --no-color || true
  "${dc[@]}" down --volumes || true
}
trap cleanup EXIT
flags=(--rm --init --network none --shm-size 1g --security-opt no-new-privileges=true
  --security-opt "seccomp=$repo/chromium-seccomp.json" --cap-drop ALL
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add SETUID --cap-add SETGID)
docker run "${flags[@]}" "$PRISM_ADAPTER_IMAGE" sh -ec '
  python -m unittest discover -p "test_*.py"
  python tests/sandbox_probe.py
  python smoke_browser.py --chrome "$PRISM_ADAPTER_CHROME"
  python smoke_client_tools.py --chrome "$PRISM_ADAPTER_CHROME"
  python smoke_multiplex.py --chrome "$PRISM_ADAPTER_CHROME" --concurrency 3 --model gpt-6.1-sol --effort xhigh --rounds 2 --reconnect-first
'
"${dc[@]}" up -d --wait --wait-timeout 120 --pull never
adapter=$("${dc[@]}" ps -q prism-adapter)
cpa=$("${dc[@]}" ps -q cli-proxy-api)
cpa_http() { docker exec -i "$cpa" bash -s -- "$@" < tests/cpa_http.sh; }
check_cpa_health() {
  cpa_http /health | python3 -c 'import json,sys; raw=sys.stdin.buffer.read(); assert raw.startswith(b"HTTP/1.1 200"); assert json.loads(raw.split(b"\r\n\r\n",1)[1])["status"]=="ok"; print("CPA loopback /health: passed")'
}
check_cpa_health
docker inspect "$adapter" | python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; assert d["State"]["Health"]["Status"]=="healthy"; assert d["HostConfig"]["NetworkMode"].startswith("container:"); assert not d["HostConfig"]["PortBindings"]'
"${dc[@]}" exec -T --user 10001:10001 prism-adapter python - <<'PY'
import os
from pathlib import Path
assert 'PRISM_ADAPTER_API_KEY' not in os.environ
root = Path('/var/lib/cliproxy-prism')
assert root.stat().st_uid == 10001 and (root.stat().st_mode & 0o777) == 0o700
assert os.access(Path.home(), os.W_OK)
(root/'persistence-marker').write_text('restart-survives')
processes = []
for p in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        cmd = p.read_bytes().split(b'\0')
        processes.append(cmd)
        if b'server.py' in cmd:
            status = (p.parent/'status').read_text()
            assert 'Uid:\t10001\t10001\t10001\t10001' in status
            assert 'CapEff:\t0000000000000000' in status
    except FileNotFoundError:
        pass
assert any(b'server.py' in p for p in processes)
assert not any(b'/opt/chromium/chrome' in p for p in processes), 'health must not launch browser'
print('permissions, zero runtime capabilities, writable HOME and lazy browser: passed')
PY
"${dc[@]}" restart prism-adapter
"${dc[@]}" up -d --wait --wait-timeout 120 --pull never
"${dc[@]}" exec -T --user 10001:10001 prism-adapter python -c 'from pathlib import Path; assert Path("/var/lib/cliproxy-prism/persistence-marker").read_text()=="restart-survives"'
"${dc[@]}" -f tests/compose.fixture.yml up -d --wait --wait-timeout 120 --pull never
# An actual request from inside the published CPA container, without bridge auth.
cpa_http /v1/responses '{"model":"gpt-6.1-sol","reasoning":{"effort":"xhigh"},"input":[{"role":"user","content":"cpa-loopback-fixture"}]}' \
  | python3 -c 'import json,sys; raw=sys.stdin.buffer.read(); assert raw.startswith(b"HTTP/1.1 200"), raw; d=json.loads(raw.split(b"\r\n\r\n",1)[1]); assert "cpa-loopback-fixture" in d["output"][0]["content"][0]["text"]; print("CPA loopback HTTP without bridge Authorization: passed")'
docker run --rm --init --network "container:$cpa" "$PRISM_ADAPTER_IMAGE" python tests/container_client.py
"${dc[@]}" exec -T --user 10001:10001 prism-adapter python - <<'PY'
import json
from pathlib import Path
root = Path('/var/lib/cliproxy-prism')
receipts = [json.loads(p.read_text()) for p in (root/'receipts').iterdir()]
assert len(receipts) == 6, len(receipts)
assert all(r['model']=='gpt-6.1-sol' and r['reasoning_effort']=='xhigh' for r in receipts)
assert not list((root/'pending').iterdir())
print('persistent receipts for six browser requests: passed')
PY
"${dc[@]}" -f tests/compose.fixture.yml restart prism-adapter
"${dc[@]}" -f tests/compose.fixture.yml up -d --wait --wait-timeout 120 --pull never
"${dc[@]}" exec -T --user 10001:10001 prism-adapter python -c 'from pathlib import Path; assert len(list(Path("/var/lib/cliproxy-prism/receipts").iterdir()))==6'
# Explicit CPA recreation must recreate/restart the sidecar into its new namespace.
old_cpa=$cpa
"${dc[@]}" -f tests/compose.fixture.yml up -d --force-recreate --wait --wait-timeout 120 --pull never
cpa=$("${dc[@]}" ps -q cli-proxy-api)
test "$cpa" != "$old_cpa"
check_cpa_health
echo 'Linux image + sandbox + Compose acceptance passed (local mock upstream only).'
