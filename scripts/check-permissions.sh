#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

if [[ ! -f .env ]]; then
    echo "ERROR: .env is missing. Run scripts/init.sh first." >&2
    exit 1
fi

puid="$(env_get PUID "$(id -u)")"
pgid="$(env_get PGID "$(id -g)")"
shared_gid="$(env_get COMFYUI_SHARED_GID "${pgid}")"
data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
workflows_path="$(resolve_host_path "$(env_get COMFYUI_WORKFLOWS_PATH ./data/workflows)" "${repo_root}")"

set +e
python3 - "${puid}" "${pgid}" "${shared_gid}" \
    "${data_path}" "${models_path}" "${workflows_path}" <<'PY'
from __future__ import annotations

import stat
import sys
from pathlib import Path

uid, gid, shared_gid = map(int, sys.argv[1:4])
data_path = Path(sys.argv[4])
items = [
    ("data root", data_path, True),
    ("cache", data_path / "cache", True),
    ("custom_nodes", data_path / "custom_nodes", True),
    ("home", data_path / "home", True),
    ("input", data_path / "input", True),
    ("output", data_path / "output", True),
    ("temp", data_path / "temp", True),
    ("user", data_path / "user", True),
    ("user/default", data_path / "user" / "default", True),
    ("models", Path(sys.argv[5]), False),
    ("workflows", Path(sys.argv[6]), True),
]
groups = {gid, shared_gid}
failures = 0


def permissions_for(path: Path) -> int:
    st = path.stat()
    mode = st.st_mode
    if uid == 0:
        return 0b111
    if st.st_uid == uid:
        return (mode >> 6) & 0b111
    if st.st_gid in groups:
        return (mode >> 3) & 0b111
    return mode & 0b111


for label, path, write_required in items:
    print(f"--- {label}: {path} ---")
    if not path.exists():
        print("FAIL  path is missing")
        failures += 1
        continue

    st = path.stat()
    print(
        f"owner={st.st_uid} group={st.st_gid} mode={stat.S_IMODE(st.st_mode):04o} "
        f"type={'directory' if path.is_dir() else 'file'}"
    )

    blocked_parent = None
    current = path.resolve(strict=False)
    for component in (current, *current.parents):
        if component.exists() and component.is_dir() and not permissions_for(component) & 0b001:
            blocked_parent = component
            break
    if blocked_parent:
        print(f"FAIL  container identity cannot traverse: {blocked_parent}")
        failures += 1
        continue

    bits = permissions_for(path)
    readable = bool(bits & 0b100)
    writable = bool(bits & 0b010)
    traversable = not path.is_dir() or bool(bits & 0b001)
    print(f"container read: {'yes' if readable else 'NO'}")
    print(f"container write: {'yes' if writable else 'NO'}")
    print(f"container traverse: {'yes' if traversable else 'NO'}")

    if not readable or not traversable or (write_required and not writable):
        failures += 1
    elif label == "models" and not writable:
        print("WARN  models are read-only; generation works, Manager model downloads do not")

sys.exit(1 if failures else 0)
PY
status=$?
set -e

cat <<EOF

Container identity: ${puid}:${pgid}; supplementary shared GID: ${shared_gid}

For new repository-local data, a typical repair is:
  sudo chown -R ${puid}:${pgid} "${data_path}"
  sudo chmod -R u+rwX,g+rwX,o-rwx "${data_path}"
  sudo find "${data_path}" -type d -exec chmod g+s {} +

For a shared model library, preserve its owner and grant the shared group access:
  sudo chgrp -R ${shared_gid} "${models_path}"
  sudo chmod -R g+rwX "${models_path}"
  sudo find "${models_path}" -type d -exec chmod g+s {} +

Review paths before running recursive permission changes.
EOF

exit "${status}"
