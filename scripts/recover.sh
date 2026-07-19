#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

usage() {
    cat <<'EOF'
Usage: scripts/recover.sh SNAPSHOT

Recover a freshly initialized ComfierUI clone from a built-in restic snapshot.

This is intentionally different from scripts/backup.sh restore:
- restore is for rolling back an existing live deployment on the same host;
- recover is for reconstructing a fresh clone while preserving the new host's
  local GPU, identity, paths, ports, networking, and backup settings.

Before running:
  1. clone ComfierUI,
  2. run scripts/init.sh,
  3. point COMFYUI_BACKUP_PATH at the existing backup directory,
  4. include compose.backup.yaml in COMPOSE_FILE,
  5. start only the backup service,
  6. run this script with an explicit known-good snapshot ID.
EOF
}

snapshot="${1:-}"
if [[ -z "${snapshot}" || $# -ne 1 ]]; then
    usage >&2
    exit 2
fi

for command_name in docker git python3; do
    require_command "${command_name}"
done
docker compose version >/dev/null

if [[ ! -f .env ]]; then
    echo "ERROR: .env is missing. Run scripts/init.sh on the recovery host first." >&2
    exit 1
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx backup; then
    echo "ERROR: The backup service is not running." >&2
    echo "Start it first with: docker compose up -d backup" >&2
    exit 1
fi

if docker compose ps --status running --services 2>/dev/null | grep -qx comfyui; then
    echo "ERROR: Fresh-clone recovery will not run while ComfyUI is already running." >&2
    echo "Use scripts/backup.sh restore for a live same-host rollback." >&2
    exit 1
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "ERROR: Tracked files in the recovery clone are modified." >&2
    echo "Commit, stash, or restore tracked changes before fresh-clone recovery." >&2
    git status --short >&2
    exit 1
fi

blueprint_value() {
    local stage_host="$1"
    local key="$2"
    local fallback="${3-}"
    local state_file="${stage_host}/backups/state/recovery-blueprint/state.env"
    local value=""

    if [[ -r "${state_file}" ]]; then
        value="$(sed -n "s/^${key}=//p" "${state_file}" | tail -n1)"
    fi
    printf '%s\n' "${value:-${fallback}}"
}

blueprint_true() {
    local stage_host="$1"
    local key="$2"
    local fallback="${3:-false}"
    bool_true "$(blueprint_value "${stage_host}" "${key}" "${fallback}")"
}

json_value() {
    local file="$1"
    local dotted_path="$2"
    local fallback="${3-}"

    python3 - "${file}" "${dotted_path}" "${fallback}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
keys = sys.argv[2].split(".") if sys.argv[2] else []
fallback = sys.argv[3]
try:
    value = json.loads(path.read_text(encoding="utf-8"))
    for key in keys:
        value = value[key]
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    print(fallback)
    raise SystemExit
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print(fallback)
else:
    print(value)
PY
}

snapshot_has_partial_policy() {
    local stage_host="$1"
    local exclude_policy="${stage_host}/backups/state/recovery-blueprint/backup-excludes.txt"
    local exclude_larger_than

    exclude_larger_than="$(blueprint_value "${stage_host}" exclude_larger_than "")"
    [[ -n "${exclude_larger_than}" ]] && return 0

    if [[ -r "${exclude_policy}" ]] && \
       grep -Ev '^[[:space:]]*($|#)' "${exclude_policy}" | grep -q .; then
        return 0
    fi

    return 1
}

copy_tree_replace() {
    local source_path="$1"
    local destination_path="$2"

    [[ -d "${source_path}" ]] || return 0
    mkdir -p "${destination_path}"
    find "${destination_path}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    command cp -a \
        --no-preserve=ownership,timestamps,xattr,context \
        "${source_path}/." "${destination_path}/"
}

copy_tree_overlay() {
    local source_path="$1"
    local destination_path="$2"

    [[ -d "${source_path}" ]] || return 0
    mkdir -p "${destination_path}"
    command cp -a --remove-destination \
        --no-preserve=ownership,timestamps,xattr,context \
        "${source_path}/." "${destination_path}/"
}

copy_path_overlay() {
    local source_path="$1"
    local destination_path="$2"

    if [[ -d "${source_path}" && ! -L "${source_path}" ]]; then
        copy_tree_overlay "${source_path}" "${destination_path}"
    elif [[ -e "${source_path}" || -L "${source_path}" ]]; then
        mkdir -p "$(dirname -- "${destination_path}")"
        rm -rf -- "${destination_path}"
        command cp -a \
            --no-preserve=ownership,timestamps,xattr,context \
            "${source_path}" "${destination_path}"
    fi
}

restore_category() {
    local stage_host="$1"
    local source_path="$2"
    local destination_path="$3"
    local include_key="$4"
    local label="$5"
    local snapshot_partial="$6"

    [[ -d "${source_path}" ]] || return 0
    blueprint_true "${stage_host}" "${include_key}" false || return 0

    if [[ "${snapshot_partial}" == false ]]; then
        echo "Restoring complete ${label}..."
        copy_tree_replace "${source_path}" "${destination_path}"
    else
        echo "Overlaying partially backed-up ${label}..."
        copy_tree_overlay "${source_path}" "${destination_path}"
    fi
}

python_volume_name() {
    local project_name
    project_name="$(env_get COMPOSE_PROJECT_NAME comfierui)"
    docker volume ls -q \
        --filter "label=com.docker.compose.project=${project_name}" \
        --filter 'label=com.docker.compose.volume=comfyui-python' \
        | head -n1
}

wait_for_comfyui_health() {
    local container_id status
    local attempts=60

    container_id="$(docker compose ps -q comfyui)"
    [[ -n "${container_id}" ]] || return 1

    while (( attempts > 0 )); do
        status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"
        case "${status}" in
            healthy|running) return 0 ;;
            unhealthy|exited|dead) return 1 ;;
        esac
        sleep 2
        ((attempts--))
    done

    return 1
}

install_custom_node_requirements() {
    echo "Reconciling restored custom-node requirements.txt files..."
    docker compose exec -T comfyui bash -lc '
set -Eeuo pipefail
found=false
while IFS= read -r -d "" requirements_file; do
    found=true
    echo "Installing: ${requirements_file}"
    /opt/venv/bin/python -m pip install -r "${requirements_file}"
done < <(find /opt/ComfyUI/custom_nodes -mindepth 2 -maxdepth 3 -type f -name requirements.txt -print0 | sort -z)
if [[ "${found}" == false ]]; then
    echo "No custom-node requirements.txt files found."
fi
/opt/venv/bin/python -m pip check
'
}

restore_python_volume() {
    local source_path="$1"
    local volume_name="$2"
    local puid pgid

    puid="$(env_get PUID "$(id -u)")"
    pgid="$(env_get PGID "$(id -g)")"

    echo "Restoring backed-up Python environment volume..."
    docker run --rm \
        -v "${volume_name}:/target" \
        -v "${source_path}:/source:ro" \
        alpine:3.22 sh -c \
        'rm -rf /target/* /target/.[!.]* /target/..?*; cp -a --no-preserve=ownership,timestamps /source/. /target/; chown -R "$1:$2" /target' \
        sh "${puid}" "${pgid}"
}

restore_selective_includes() {
    local stage_host="$1"
    local data_path="$2"
    local models_path="$3"
    local workflows_path="$4"
    local extra_models_path="$5"
    local policy_file="${stage_host}/backups/state/recovery-blueprint/backup-includes.txt"
    local selected_path relative_path source_path destination_path

    [[ -r "${policy_file}" ]] || return 0

    while IFS= read -r selected_path || [[ -n "${selected_path}" ]]; do
        case "${selected_path}" in
            ''|\#*) continue ;;
            /source/repo|/source/repo/*)
                echo "Skipping selective repository path during fresh recovery: ${selected_path}"
                echo "Git and the recovery blueprint are authoritative for deployment source."
                continue
                ;;
            /source/data|/source/data/*)
                relative_path="${selected_path#/source/data}"
                source_path="${stage_host}${selected_path}"
                destination_path="${data_path}${relative_path}"
                ;;
            /source/workflows|/source/workflows/*)
                relative_path="${selected_path#/source/workflows}"
                source_path="${stage_host}${selected_path}"
                destination_path="${workflows_path}${relative_path}"
                ;;
            /source/models|/source/models/*)
                relative_path="${selected_path#/source/models}"
                source_path="${stage_host}${selected_path}"
                destination_path="${models_path}${relative_path}"
                ;;
            /source/extra-models|/source/extra-models/*)
                if [[ -z "${extra_models_path}" ]]; then
                    echo "ERROR: Snapshot contains selectively backed-up extra models, but this host has no COMFYUI_EXTRA_MODELS_PATH." >&2
                    return 1
                fi
                relative_path="${selected_path#/source/extra-models}"
                source_path="${stage_host}${selected_path}"
                destination_path="${extra_models_path}${relative_path}"
                ;;
            /source/python|/source/python/*)
                echo "WARN: Selective Python-volume paths are not applied during fresh recovery." >&2
                echo "      The core venv is rebuilt and Python inventory differences are reported afterward." >&2
                continue
                ;;
            *)
                echo "WARN: Ignoring unsupported selective recovery path: ${selected_path}" >&2
                continue
                ;;
        esac

        if [[ -e "${source_path}" || -L "${source_path}" ]]; then
            echo "Restoring selective path: ${selected_path}"
            copy_path_overlay "${source_path}" "${destination_path}"
        fi
    done < "${policy_file}"
}

report_inventory_gaps() {
    local stage_host="$1"
    local models_path="$2"
    local extra_models_path="$3"
    local pip_json="$4"

    python3 - \
        "${stage_host}/backups/state/recovery-blueprint/models.tsv" "${models_path}" \
        "${stage_host}/backups/state/recovery-blueprint/extra-models.tsv" "${extra_models_path}" \
        "${stage_host}/backups/state/recovery-blueprint/python-packages.tsv" "${pip_json}" <<'PY'
import json
import sys
from pathlib import Path

models_inventory = Path(sys.argv[1])
models_root = Path(sys.argv[2])
extra_inventory = Path(sys.argv[3])
extra_root_text = sys.argv[4]
python_inventory = Path(sys.argv[5])
pip_json = Path(sys.argv[6])


def inventory_paths(path: Path):
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        result.append(parts[-1])
    return result


def report_files(label: str, inventory: Path, root_text: str):
    entries = inventory_paths(inventory)
    if not entries:
        print(f"{label}: inventory empty")
        return
    if not root_text:
        print(f"{label}: {len(entries)} inventoried file(s); no host path is attached on this recovery host")
        return
    root = Path(root_text)
    missing = [entry for entry in entries if not (root / entry).exists()]
    print(f"{label}: {len(entries) - len(missing)}/{len(entries)} inventoried file(s) present")
    for entry in missing[:10]:
        print(f"  missing: {entry}")
    if len(missing) > 10:
        print(f"  ... and {len(missing) - 10} more")


report_files("Writable models", models_inventory, str(models_root))
report_files("Extra/legacy models", extra_inventory, extra_root_text)

expected = {}
if python_inventory.is_file():
    for line in python_inventory.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            expected[parts[0].lower()] = parts[1]

actual = {}
try:
    for item in json.loads(pip_json.read_text(encoding="utf-8")):
        actual[item["name"].lower()] = item["version"]
except (OSError, ValueError, KeyError, TypeError):
    pass

if expected and actual:
    missing = sorted(name for name in expected if name not in actual)
    mismatched = sorted(
        (name, expected[name], actual[name])
        for name in expected.keys() & actual.keys()
        if expected[name] != actual[name]
    )
    print(f"Python packages: {len(expected) - len(missing)}/{len(expected)} inventoried package names present")
    for name in missing[:10]:
        print(f"  missing package: {name}=={expected[name]}")
    if len(missing) > 10:
        print(f"  ... and {len(missing) - 10} more missing package names")
    if mismatched:
        print(f"Python version differences: {len(mismatched)}")
        for name, wanted, actual_version in mismatched[:10]:
            print(f"  {name}: snapshot={wanted}, recovered={actual_version}")
        if len(mismatched) > 10:
            print(f"  ... and {len(mismatched) - 10} more version differences")
PY
}

echo "Staging recovery snapshot ${snapshot}..."
stage_output="$(bash scripts/backup.sh stage "${snapshot}")"
printf '%s\n' "${stage_output}"
stage_host="$(printf '%s\n' "${stage_output}" | sed -n 's/^Staged snapshot at: //p' | tail -n1)"

if [[ -z "${stage_host}" || ! -d "${stage_host}" ]]; then
    echo "ERROR: Could not resolve staged snapshot directory." >&2
    exit 1
fi

blueprint_dir="${stage_host}/backups/state/recovery-blueprint"
state_file="${blueprint_dir}/state.env"
container_state="${blueprint_dir}/container-state.json"

if [[ ! -r "${state_file}" ]]; then
    echo "ERROR: Snapshot does not contain a recovery blueprint state.env." >&2
    exit 1
fi

snapshot_commit="$(blueprint_value "${stage_host}" repository_commit "")"
if [[ -z "${snapshot_commit}" || "${snapshot_commit}" == unknown ]]; then
    echo "ERROR: Snapshot does not record a usable ComfierUI repository commit." >&2
    exit 1
fi

snapshot_accelerator="$(blueprint_value "${stage_host}" COMFYUI_ACCELERATOR unknown)"
host_accelerator="$(env_get COMFYUI_ACCELERATOR unknown)"
extra_models_setting="$(env_get COMFYUI_EXTRA_MODELS_PATH "")"

if blueprint_true "${stage_host}" include_extra_models false && [[ -z "${extra_models_setting}" ]]; then
    echo "ERROR: Snapshot contains a complete extra-model payload, but this host has no COMFYUI_EXTRA_MODELS_PATH." >&2
    exit 1
fi

if [[ -z "${extra_models_setting}" && -r "${blueprint_dir}/backup-includes.txt" ]] && \
   grep -Ev '^[[:space:]]*($|#)' "${blueprint_dir}/backup-includes.txt" | grep -q '^/source/extra-models\(/\|$\)'; then
    echo "ERROR: Snapshot contains selective extra-model payloads, but this host has no COMFYUI_EXTRA_MODELS_PATH." >&2
    exit 1
fi

echo
echo "=== Recovery source ==="
echo "Snapshot: ${snapshot}"
echo "ComfierUI repository commit: ${snapshot_commit}"
echo "Snapshot accelerator: ${snapshot_accelerator}"
echo "Recovery-host accelerator: ${host_accelerator}"
echo
echo "Host-local .env settings will be preserved."
echo "Portable application pins will be imported from the recovery blueprint."

# Staging is complete. Remove the recovery clone's backup containers before any
# Python-volume surgery or Git checkout. The original backup repository remains
# untouched because it is a host bind mount.
docker compose stop backup >/dev/null 2>&1 || true
docker compose rm -sf backup backup-init >/dev/null 2>&1 || true

restore_python=false
if blueprint_true "${stage_host}" include_python false && \
   [[ -d "${stage_host}/source/python" ]] && \
   [[ "${snapshot_accelerator}" == "${host_accelerator}" ]]; then
    restore_python=true
fi

existing_python_volume="$(python_volume_name)"
if [[ -n "${existing_python_volume}" ]]; then
    echo "Removing fresh recovery-clone Python volume so the recovered image can initialize it cleanly..."
    docker volume rm "${existing_python_volume}" >/dev/null
fi

if [[ "$(git rev-parse HEAD)" != "${snapshot_commit}" ]]; then
    if ! git cat-file -e "${snapshot_commit}^{commit}" 2>/dev/null; then
        echo "Fetching recorded ComfierUI repository commit ${snapshot_commit}..."
        git fetch origin "${snapshot_commit}"
    fi
    echo "Checking out recorded ComfierUI repository commit in detached-HEAD mode..."
    git switch --detach "${snapshot_commit}"
else
    echo "Recovery clone already matches the recorded ComfierUI repository commit."
fi

if [[ -r "${container_state}" ]]; then
    comfyui_commit="$(json_value "${container_state}" comfyui.commit "")"
    torch_version="$(json_value "${container_state}" image_build.requested_versions.torch "")"
    torchvision_version="$(json_value "${container_state}" image_build.requested_versions.torchvision "")"
    torchaudio_version="$(json_value "${container_state}" image_build.requested_versions.torchaudio "")"
else
    comfyui_commit="$(blueprint_value "${stage_host}" COMFYUI_COMMIT "")"
    torch_version="$(blueprint_value "${stage_host}" PYTORCH_VERSION "")"
    torchvision_version="$(blueprint_value "${stage_host}" TORCHVISION_VERSION "")"
    torchaudio_version="$(blueprint_value "${stage_host}" TORCHAUDIO_VERSION "")"
    torch_version="${torch_version%%+*}"
    torchvision_version="${torchvision_version%%+*}"
    torchaudio_version="${torchaudio_version%%+*}"
fi

if [[ -n "${comfyui_commit}" && "${comfyui_commit}" != unknown ]]; then
    env_set COMFYUI_REF "${comfyui_commit}"
else
    env_set COMFYUI_REF "$(blueprint_value "${stage_host}" COMFYUI_REF v0.28.0)"
fi
[[ -n "${torch_version}" && "${torch_version}" != unknown ]] && env_set PYTORCH_VERSION "${torch_version}"
[[ -n "${torchvision_version}" && "${torchvision_version}" != unknown ]] && env_set TORCHVISION_VERSION "${torchvision_version}"
[[ -n "${torchaudio_version}" && "${torchaudio_version}" != unknown ]] && env_set TORCHAUDIO_VERSION "${torchaudio_version}"

env_set COMFYUI_MANAGER_ENABLED "$(blueprint_value "${stage_host}" COMFYUI_MANAGER_ENABLED true)"
env_set COMFYUI_MANAGER_LEGACY_UI "$(blueprint_value "${stage_host}" COMFYUI_MANAGER_LEGACY_UI true)"
env_set COMFYUI_MANAGER_SECURITY_LEVEL "$(blueprint_value "${stage_host}" COMFYUI_MANAGER_SECURITY_LEVEL normal)"
env_set COMFYUI_MANAGER_NETWORK_MODE "$(blueprint_value "${stage_host}" COMFYUI_MANAGER_NETWORK_MODE personal_cloud)"

if [[ "${snapshot_accelerator}" != "${host_accelerator}" ]]; then
    echo "WARN: Snapshot accelerator differs from this host." >&2
    echo "      Preserving this host's accelerator/base-image/Torch index and rebuilding the Python environment." >&2
    restore_python=false
fi

data_path="$(resolve_host_path "$(env_get COMFYUI_DATA_PATH ./data)" "${repo_root}")"
models_path="$(resolve_host_path "$(env_get COMFYUI_MODELS_PATH ./data/models)" "${repo_root}")"
workflows_path="$(resolve_host_path "$(env_get COMFYUI_WORKFLOWS_PATH ./data/workflows)" "${repo_root}")"
extra_models_path=""
if [[ -n "${extra_models_setting}" ]]; then
    extra_models_path="$(resolve_host_path "${extra_models_setting}" "${repo_root}")"
fi

snapshot_partial=false
if snapshot_has_partial_policy "${stage_host}"; then
    snapshot_partial=true
    echo "Snapshot used an exclude pattern or file-size ceiling; partial categories will be overlaid."
fi

echo
echo "=== Restoring unique state ==="
restore_category \
    "${stage_host}" "${stage_host}/source/data/custom_nodes" "${data_path}/custom_nodes" \
    include_custom_nodes "custom nodes" "${snapshot_partial}"
restore_category \
    "${stage_host}" "${stage_host}/source/data/user" "${data_path}/user" \
    include_user "ComfyUI and Manager user state" "${snapshot_partial}"
restore_category \
    "${stage_host}" "${stage_host}/source/data/input" "${data_path}/input" \
    include_input "input images" "${snapshot_partial}"
restore_category \
    "${stage_host}" "${stage_host}/source/data/output" "${data_path}/output" \
    include_output "output images" "${snapshot_partial}"
restore_category \
    "${stage_host}" "${stage_host}/source/workflows" "${workflows_path}" \
    include_workflows "workflows" "${snapshot_partial}"
restore_category \
    "${stage_host}" "${stage_host}/source/models" "${models_path}" \
    include_models "writable model library" "${snapshot_partial}"

if [[ -n "${extra_models_path}" ]]; then
    restore_category \
        "${stage_host}" "${stage_host}/source/extra-models" "${extra_models_path}" \
        include_extra_models "extra/legacy model library" "${snapshot_partial}"
fi

restore_selective_includes \
    "${stage_host}" "${data_path}" "${models_path}" "${workflows_path}" "${extra_models_path}"

mkdir -p "${data_path}/user/default" "${models_path}" "${workflows_path}"
[[ -n "${extra_models_path}" ]] && mkdir -p "${models_path}/external"

echo
echo "=== Building recovered core ==="
echo "ComfyUI ref: $(env_get COMFYUI_REF)"
echo "PyTorch pins: $(env_get PYTORCH_VERSION) / $(env_get TORCHVISION_VERSION) / $(env_get TORCHAUDIO_VERSION)"
echo "Recovery-host Torch index: $(env_get TORCH_INDEX_URL)"
echo "Recovery-host base image: $(env_get COMFYUI_BASE_IMAGE)"
docker compose build comfyui

if [[ "${restore_python}" == true ]]; then
    docker compose create comfyui >/dev/null
    recovered_python_volume="$(python_volume_name)"
    if [[ -z "${recovered_python_volume}" ]]; then
        echo "ERROR: Could not resolve Python volume after creating recovered ComfyUI service." >&2
        exit 1
    fi
    restore_python_volume "${stage_host}/source/python" "${recovered_python_volume}"
else
    if blueprint_true "${stage_host}" include_python false; then
        echo "Python volume payload was not reused; rebuilding it for this recovery host."
    else
        echo "Python volume was not backed up; rebuilding the pinned core environment."
    fi
fi

echo
echo "=== Starting recovered ComfyUI ==="
docker compose up -d --force-recreate comfyui
if ! wait_for_comfyui_health; then
    echo "ERROR: Recovered ComfyUI did not become healthy." >&2
    docker compose logs --tail=120 comfyui >&2 || true
    exit 1
fi

install_custom_node_requirements

echo "Restarting ComfyUI after custom-node dependency reconciliation..."
docker compose restart comfyui >/dev/null
if ! wait_for_comfyui_health; then
    echo "ERROR: ComfyUI did not become healthy after custom-node dependency reconciliation." >&2
    docker compose logs --tail=120 comfyui >&2 || true
    exit 1
fi

if docker compose config --services 2>/dev/null | grep -qx backup; then
    echo "Starting recovery clone backup service..."
    docker compose up -d backup >/dev/null
fi

pip_json="$(mktemp)"
trap 'rm -f "${pip_json}"' EXIT
docker compose exec -T comfyui /opt/venv/bin/python -m pip list --format=json > "${pip_json}"

echo
echo "=== Recovery reconciliation report ==="
report_inventory_gaps "${stage_host}" "${models_path}" "${extra_models_path}" "${pip_json}"

echo
echo "=== Recovery complete ==="
echo "Recovered snapshot: ${snapshot}"
echo "ComfierUI checkout: $(git rev-parse HEAD)"
echo "Staged snapshot: ${stage_host}"
echo "Host-local .env values were preserved; portable application pins came from the blueprint."
echo "Tracked deployment source is pinned to the snapshot's recorded Git commit."
echo
docker compose ps
