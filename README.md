# Prism Adapter

Prebuilt standalone Linux amd64/arm64 container for CLIProxyAPI.

- Image: `ghcr.io/power12317/prism-adapter` (`latest` and release tags)
- **[中文部署说明](README_CN.md)** — download deployment files and pull the image; no local build.
- [Releases](https://github.com/power12317/prism-adapter/releases) include both Compose variants, the seccomp profile and immutable image digests.
- [CI](https://github.com/power12317/prism-adapter/actions) validates native Linux containers before publishing.


This adapter implements the Prism behavior of sub2api production
`0ae36e501952000c5c910a2e616c6e0861f66a49`, with selected v2.10.1 compatibility and
resource updates. Source provenance and licensing are in
[UPSTREAM.md](UPSTREAM.md).

## Routing and capabilities

CPA exposes one switch in network settings, disabled by default:

```yaml
codex:
  prism:
    enabled: false
```

Enabling the switch routes eligible Codex OAuth credentials through Prism for
the complete sub2api catalog: `gpt-6.1-sol`, `gpt-5.6-sol`, `gpt-5.6-terra`, and
`gpt-6-luna`. Routing applies after model alias and thinking-suffix resolution.
Other models retain their normal Codex/Basispoints route. Prism requests do not
fall back to another protocol on failure.

The switch registers these models even when the native Codex catalog has not
listed them yet. Hot reload updates the model list and scheduler without a
restart. OAuth login uses the existing flow.

Supported reasoning levels are low, medium, high, and xhigh (medium when omitted),
subject to the options actually available on the Prism account. Model/effort
selection is refreshed when opening a chat and preserved on continued turns. Other models and native WebSocket,
compact, image, background, and structured-output protocols are not implemented
by the upstream Prism adapter. WebSocket selection excludes accounts whose mapped
model uses Prism; other accounts remain available for WebSocket requests.

Requests may use `reasoning.summary=detailed`; Prism does not manufacture a
reasoning summary. Complete prompts, including tools, instructions and history,
are split losslessly using an 86,000-byte JSON-escaped UTF-8 transport budget
(`PRISM_MAX_PROMPT_BYTES`, the existing setting). Earlier parts only acknowledge
receipt; the final part produces the client response. All parts retain the
requested model and effort. An explicit upstream size refusal halves the segment
budget once and recuts only the undelivered suffix. Unknown outcomes are not replayed.
CPA applies ordinary payload rules once after built-in model, reasoning and stream
preparation, preserving configured overrides and removals through dispatch.

The client-tool bridge is enabled by default for the same four-model catalog as
text requests, with function/custom tools, nested namespaces, additional_tools,
tool_choice, and parallel_tool_calls. It preserves the requested model and effort;
the upstream restriction to gpt-6.1-sol has been removed. Tools execute at the
client. Subsequent requests carry the complete call/result history and original
tool catalog. Matching history prefixes continue the native Prism conversation
with only new entries, using its response ID and listen snapshot. Account,
caller, client session, model, effort and instruction/catalog identity scope this
reuse. Changed history starts a fresh chat. A confirmed expired continuation or
size refusal can fall back to a full, segmented replay before any new part succeeds.
Descriptions, instructions and old history are never shortened or summarized.
Both browser modes support the relay; CPA needs no additional setting or update.

Prism returns completed responses rather than token deltas. SSE includes completed
text/tool items and a final response. Usage remains null; CPA publishes
`usage_unavailable: true` with null tokens/token_breakdown to its usage queue while
retaining request outcomes and latency. No authoritative usage is estimated.

## Sessions and execution modes

Caller scope comes from authenticated CPA request context. Standard session/thread
headers and Codex client_metadata identify conversations, with thread identity
preferred over session identity. Client-supplied private X-Prism headers never
select another caller's state. Requests lacking explicit identity create a fresh
project. Text conversations reuse their project but open a new chat tab before
submitting full client history. Cache TTL is 300 seconds, maximum age 900 seconds,
and credential changes replace contexts after active work drains.

The adapter retains sub2api's default browser execution mode and optional
multiplex implementation. Multiplex defaults to 20 active requests, 20 per
account, 30 queued requests, and one project preparation at a time.

The upstream bounds are 30 active requests, 60 queued requests, and 1-2 preparation
pages. A conversation remains sequential; independent conversations may overlap.
The current multiplex entrypoint holds one account context at a time. It does not
claim to implement a multi-account browser pool. A waiting account receives busy
while the current context is occupied. The adapter's upstream deployment controls
remain internal to that process.

Multiplex hands polling from the temporary editor to a stationary official page,
preserves the official fetch/Sentinel flow and asset cache, and reclaims closed
editor resources. Memory pressure waits up to 30 seconds at the upstream default
750 MiB threshold. Deployments can adjust `PRISM_ADAPTER_MEMORY_LIMIT_MIB`; this
positive integer controls both new-page admission and idle-context reclamation
and should remain below the process's hard memory limit. It does not change
concurrency limits. Runtime-start throttling cools new starts for at least 60 seconds.
Already submitted work continues. Explicit pre-execution sandbox
reconnect follows the official page for up to three start attempts; unknown
outcomes are never automatically resubmitted. Pending records are per conversation.
Projects remain in the Prism workspace; upstream does not automatically delete them.

## Container deployment

Download the deployment archive from a release. For a new deployment, put your
existing CPA `config.yaml`, `auths/`, and `logs/` next to the extracted files:

```sh
docker compose -f docker-compose.prism.yml pull
docker compose -f docker-compose.prism.yml up -d --wait
```

For an existing CPA or Codex sidecar stack, merge `docker-compose.prism-overlay.yml`
into the **same Compose project** (same directory and project name):

```sh
docker compose -f docker-compose.yml -f docker-compose.prism-overlay.yml up -d --no-build --wait prism-adapter
```

The existing service must be named `cli-proxy-api`. Do not start the standalone
file alongside an existing CPA. Keep the required `chromium-seccomp.json` beside
the first Compose file. Compose 2.24.4+ is used for CI; deployment needs 2.20+.
The entrypoint initializes `./prism-data` and drops to UID/GID 10001; never
force the adapter server to run as root. Browser and Python packages are already
installed. Startup does not download code, packages or browsers.

The adapter shares CPA's network namespace and binds only `127.0.0.1:8319`.
It has no host port, bridge API key, or CPA auth-directory mount. CPA forwards
OAuth/account/caller/session headers per request. `/health` reports process
liveness only; Chromium starts on demand. The Compose `init` process reaps
children. The 1 GiB `/dev/shm` allocation provides browser shared-memory capacity,
not a reservation or a browser concurrency/memory limit. Existing runtime
defaults remain unchanged.

The seccomp profile derives from Moby v27.5.1's default profile, adding only
`clone`, `setns`, `unshare` and `chroot` permissions needed by Playwright's user-namespace
sandbox. `chroot` is allowed as a syscall for the nested namespace; the container
does not receive CAP_SYS_CHROOT. It retains the default syscall deny behavior, including clone3 fallback.
See `COPYING.SECCOMP`. No privileged mode, SYS_ADMIN, or sandbox disabling is used.
The application runs with zero effective Linux capabilities and
`no-new-privileges`. Only mount initialization uses CHOWN/DAC_OVERRIDE/SETUID/SETGID.
Hosts must support unprivileged user namespaces; a host LSM policy can still
restrict them. Diagnose policy denials rather than disabling Chromium sandboxing.

## Reproducibility and verification

The official Playwright Python 1.63.0 Noble multi-architecture base is pinned by
manifest digest in `Dockerfile`. It supplies Python 3.12 and matching Chromium.
`requirements.lock` pins transitive Python packages with hashes. There is no
runtime package installation. OCI labels link each published image to its source
revision, release version and LGPL license.

Each native architecture runs all Python regressions, checks `chrome://sandbox`,
executes real Chromium text/tool/multiplex fixtures, and starts the production
Compose file with the released CPA image. It verifies loopback HTTP from CPA,
JSON/SSE and function → custom → final with gpt-6.1-sol/xhigh, root-created bind
mount permissions, persistence, lazy browser startup, and CPA namespace recreation.
The fixture upstream is local; no genuine OAuth credential or Prism request is used.

For contributors with Docker, `bash tests/acceptance.sh IMAGE` runs the same checks
against a built image (pull the CPA version in Compose first). Deployment users
only pull releases. Real-account testing is documented in `README_CN.md` and is
separate from CI's mock-upstream verification.

## Source and license

Runtime modules, fixtures and the original 81 regressions were copied from CPA
`cebd2296f711975734950914cf210e8512527d9e` (v2026.10.10-3). The exact imported
file hashes are in `cpa-source-manifest.json`; earlier sub2api provenance remains
in `upstream-manifest.json` and `UPSTREAM.md`. Application code remains LGPL-3.0-only.
Source for any released image is available at its matching Git tag. See `COPYING`
and `COPYING.LESSER`; the Moby seccomp profile has its own Apache-2.0 notice.
