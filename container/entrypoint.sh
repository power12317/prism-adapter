#!/bin/sh
set -eu
umask 077
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$PRISM_ADAPTER_STATE_DIR" "$HOME"
    # -P prevents traversing symlinks in persisted state.
    chown -R -P 10001:10001 "$PRISM_ADAPTER_STATE_DIR" "$HOME"
    chmod 700 "$PRISM_ADAPTER_STATE_DIR" "$HOME"
    exec python /opt/prism-adapter/container/drop_privileges.py "$@"
fi
exec "$@"
