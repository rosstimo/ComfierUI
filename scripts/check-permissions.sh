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
extra_models_setting="$(env_get COMFYUI_EXTRA_MODELS_PATH "" .env)"
extra_models_path=""
if [[ -n "${extra_models_setting}" ]]; then
    extra_models_path="$(resolve_host_path "${extra_models_setting}" "${repo_root}")"
fi

set +e
python3 - "${puid}" "${pgid}" "${shared_gid}" \
    "${data_path}" "${models_path}" "${workflows_path}" "${extra_models_path}" <<'PY'
from __future__ import annotations

import stat
import sys
from pathlib import Path

uid, gid, shared_gid = map(int, sys.argv[1:4])
data_path = Path(sys.argv[4])
items = [
    ("data root", data_path, True, False),
    ("cache", data_path / "cache", True, False),
    ("custom_nodes", data_path / "custom_nodes", True, False),
    ("home", data_path / "home", True, False),
    ("input", data_path / "input", True, False),
    ("output", data_path / "output", True, False),
    ("temp", data_path / "temp", True, False),
    ("user", data_path / "user", True, False),
    ("user/default", data_path / "user" / "default", True, False),
    ("models", Path(sys.argv[5]), False, False),
    ("workflows", Path(sys.argv[6]), True, False),
]
if sys.argv[7]:
    items.append(("extra models", Path(sys.argv[7]), False, True))
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


for label, path, write_required, mount_read_only in items:
    suffix = " (read-only mount)" if mount_read_only else ""
    print(f"--- {label}{suffix}: {path} ---")
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
    if mount_read_only:
        print(
            "container write: blocked by read-only mount "
            f"(host path permission: {'yes' if writable else 'NO'})"
        )
    else:
        print(f"container write: {'yes' if writable else 'NO'}")
    print(f"container traverse: {'yes' if traversable else 'NO'}")

    if not readable or not traversable or (write_required and not writable):
        failures += 1
    elif label == "models" and not writable:
        print("WARN  built-in models are read-only; generation works, Manager model downloads do not")

sys.exit(1 if failures else 0)
PY
status=$?
set -e

cat <<EOF

Container identity: ${puid}:${pgid}; supplementary shared GID: ${shared_gid}

PUID/PGID set the container's primary user and group. COMFYUI_SHARED_GID adds
one supplementary group so the container can use existing group permissions
without changing host ownership. For example, a shared directory owned by group
1002 can be accessed by setting COMFYUI_SHARED_GID=1002 when its mode permits it.

For new repository-local data, a typical repair is:
  sudo chown -R ${puid}:${pgid} "${data_path}"
  sudo chmod -R u+rwX,g+rwX,o-rwx "${data_path}"
  sudo find "${data_path}" -type d -exec chmod g+s {} +

chown/chgrp change host ownership; chmod changes host permission bits. Prefer
COMFYUI_SHARED_GID for an existing shared group before changing ownership.

The built-in model tree should normally remain writable for Manager downloads:
  ${models_path}
EOF

if [[ -n "${extra_models_path}" ]]; then
    cat <<EOF

The extra model library is intentionally mounted read-only. Preserve its owner
and grant the container identity read/traverse access as needed:
  ${extra_models_path}
EOF
fi

cat <<'EOF'

Review paths before running recursive permission changes.
EOF

exit "${status}"
