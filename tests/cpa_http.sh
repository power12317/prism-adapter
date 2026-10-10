#!/usr/bin/env bash
# Use Debian's existing bash TCP support; CPA intentionally has no curl/wget.
set -euo pipefail
path=${1:?HTTP path required}
body=${2:-}
exec 3<>/dev/tcp/127.0.0.1/8319
if [ -n "$body" ]; then
  printf 'POST %s HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Type: application/json\r\nX-Prism-OAuth-Token: synthetic-token\r\nX-Prism-Account-ID: 300\r\nContent-Length: %s\r\n\r\n%s' "$path" "${#body}" "$body" >&3
else
  printf 'GET %s HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' "$path" >&3
fi
cat <&3
