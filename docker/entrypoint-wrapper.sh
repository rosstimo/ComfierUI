#!/usr/bin/env bash
set -Eeuo pipefail

venv_root=/opt/venv

# The backup sidecar also mounts the persistent Python volume so it can inventory
# or optionally back it up. On a brand-new deployment that can create the named
# volume before ComfyUI ever starts, leaving it empty instead of receiving Docker's
# normal image-to-volume copy-up. Bootstrap an empty volume in place; the normal
# entrypoint will then see the missing build ID and install the pinned core stack.
if [[ ! -x "${venv_root}/bin/python" ]]; then
    echo "Persistent Python environment is empty; bootstrapping a fresh virtual environment..."
    python3 -m venv --clear "${venv_root}"
fi

exec /usr/local/bin/comfierui-entrypoint "$@"
