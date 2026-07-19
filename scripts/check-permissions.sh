#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

verbose=false
case "${1:-}" in
    "") ;;
    --verbose) verbose=true ;;
    *)
        echo "Usage: $0 [--verbose]" >&2
        exit 2
        ;;
esac

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
    "${data_path}" "${models_path}" "${workflows_path}" "${extra_models_path}" "${verbose}" <<'PY'
from __future__ import annotations

import grp
import stat
import sys
from pathlib import Path

uid, gid, shared_gid = map(int, sys.argv[1:4])
data_path = Path(sys.argv[4])
models_path = Path(sys.argv[5])
workflows_path = Path(sys.argv[6])
extra_models_raw = sys.argv[7]
verbose = sys.argv[8].lower() == "true"

items = [
    ("data root", data_path, True, False, True),
    ("cache", data_path / "cache", True, False, True),
    ("custom_nodes", data_path / "custom_nodes", True, False, True),
    ("home", data_path / "home", True, False, True),
    ("input", data_path / "input", True, False, True),
    ("output", data_path / "output", True, False, True),
    ("temp", data_path / "temp", True, False, True),
    ("user", data_path / "user", True, False, True),
    ("user/default", data_path / "user" / "default", True, False, True),
    ("models", models_path, False, False, False),
    ("workflows", workflows_path, True, False, False),
]
if extra_models_raw:
    items.append(("extra models", Path(extra_models_raw), False, True, False))

groups = {gid, shared_gid}
failures = 0
local_repair_needed = False
suggested_gids: set[int] = set()


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


def gid_name(value: int) -> str:
    try:
        return grp.getgrgid(value).gr_name
    except KeyError:
        return "unknown-group"


def maybe_suggest_gid(path: Path, required_bits: int) -> None:
    st = path.stat()
    candidate = st.st_gid
    group_bits = (st.st_mode >> 3) & 0b111
    if candidate not in groups and group_bits & required_bits == required_bits:
        if candidate not in suggested_gids:
            print(
                f"SUGGEST  set COMFYUI_SHARED_GID={candidate} "
                f"({gid_name(candidate)}) to use existing group permissions"
            )
            suggested_gids.add(candidate)


for label, path, write_required, mount_read_only, local_path in items:
    suffix = " (read-only mount)" if mount_read_only else ""
    display = f"{label}{suffix}"

    if not path.exists():
        print(f"FAIL  {display}: path is missing: {path}")
        failures += 1
        local_repair_needed |= local_path
        continue

    st = path.stat()
    if verbose:
        print(
            f"INFO  {display}: {path} "
            f"owner={st.st_uid} group={st.st_gid} "
            f"mode={stat.S_IMODE(st.st_mode):04o}"
        )

    blocked_parent = None
    current = path.resolve(strict=False)
    for component in (current, *current.parents):
        if component.exists() and component.is_dir() and not permissions_for(component) & 0b001:
            blocked_parent = component
            break

    if blocked_parent:
        blocked = blocked_parent.stat()
        print(f"FAIL  {display}: container cannot traverse {blocked_parent}")
        if not verbose:
            print(
                f"      owner={blocked.st_uid} group={blocked.st_gid} "
                f"mode={stat.S_IMODE(blocked.st_mode):04o}"
            )
        maybe_suggest_gid(blocked_parent, 0b001)
        failures += 1
        local_repair_needed |= local_path
        continue

    bits = permissions_for(path)
    readable = bool(bits & 0b100)
    writable = bool(bits & 0b010)
    traversable = not path.is_dir() or bool(bits & 0b001)

    required_bits = 0b100 | (0b001 if path.is_dir() else 0)
    if write_required:
        required_bits |= 0b010

    if not readable or not traversable or (write_required and not writable):
        access = []
        if not readable:
            access.append("read")
        if not traversable:
            access.append("traverse")
        if write_required and not writable:
            access.append("write")
        print(f"FAIL  {display}: missing container access: {', '.join(access)}")
        if not verbose:
            print(
                f"      path={path} owner={st.st_uid} group={st.st_gid} "
                f"mode={stat.S_IMODE(st.st_mode):04o}"
            )
        maybe_suggest_gid(path, required_bits)
        failures += 1
        local_repair_needed |= local_path
        continue

    if mount_read_only:
        print(f"PASS  {display}: read/traverse available; Docker mount blocks writes")
    elif label == "models" and not writable:
        print(f"WARN  {display}: readable but not writable; Manager model downloads will fail")
        maybe_suggest_gid(path, 0b111 if path.is_dir() else 0b110)
    elif write_required:
        print(f"PASS  {display}: read/write/traverse available")
    else:
        print(f"PASS  {display}: read/traverse available")

if failures:
    print()
    print(f"Container identity: {uid}:{gid}; supplementary shared GID: {shared_gid}")
    print("COMFYUI_SHARED_GID adds one supplementary numeric group without changing host ownership.")
    print("Use the SUGGEST line above when an existing shared path's group permissions already fit.")
    if local_repair_needed:
        print("LOCAL_REPAIR_NEEDED")
    sys.exit(2 if local_repair_needed else 1)

print()
print(f"Permissions OK for container identity {uid}:{gid} with shared GID {shared_gid}.")
PY
status=$?
set -e

if [[ "${status}" -eq 2 ]]; then
    cat <<EOF

One or more repository-local data paths need repair. Review these paths before
running recursive changes. A typical repair for the repository-local data root is:

  sudo chown -R ${puid}:${pgid} "${data_path}"
  sudo chmod -R u+rwX,g+rwX,o-rwx "${data_path}"
  sudo find "${data_path}" -type d -exec chmod g+s {} +

Prefer COMFYUI_SHARED_GID for existing shared workflow/model libraries before
changing their ownership.
EOF
elif [[ "${status}" -ne 0 ]]; then
    cat <<'EOF'

Review the failed external/shared paths. Prefer COMFYUI_SHARED_GID when their
existing group permissions already provide the needed access. Avoid recursive
chown/chmod on shared libraries unless changing host ownership is intentional.
EOF
fi

exit "${status}"
