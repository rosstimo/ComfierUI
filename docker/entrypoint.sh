#!/usr/bin/env bash
set -Eeuo pipefail

comfy_root=/opt/ComfyUI
venv_root=/opt/venv
manager_config_file="${comfy_root}/user/__manager/config.ini"
image_build_id_file=/opt/comfierui-build-id
venv_build_id_file=/opt/venv/.comfierui-build-id
build_state_file=/opt/comfierui-build-state.env
runtime_state_file="${comfy_root}/user/.comfierui/runtime-state.json"

umask "${COMFYUI_UMASK:-0002}"

mkdir -p \
    /data/cache/huggingface \
    /data/cache/pip \
    /data/cache/torch \
    /data/home \
    /tmp/ckpts \
    "${comfy_root}/custom_nodes" \
    "${comfy_root}/input" \
    "${comfy_root}/models" \
    "${comfy_root}/output" \
    "${comfy_root}/temp" \
    "${comfy_root}/user/__manager" \
    "${comfy_root}/user/default/workflows"

if [[ ! -x "${venv_root}/bin/python" ]]; then
    echo "ERROR: The persistent Python environment is missing or corrupt." >&2
    echo "Remove the comfyui-python volume, then rebuild the service." >&2
    exit 1
fi

manager_security_level="${COMFYUI_MANAGER_SECURITY_LEVEL:-normal}"
manager_network_mode="${COMFYUI_MANAGER_NETWORK_MODE:-personal_cloud}"

case "${manager_security_level}" in
    strong|normal|normal-|weak) ;;
    *)
        echo "ERROR: Invalid COMFYUI_MANAGER_SECURITY_LEVEL: ${manager_security_level}" >&2
        exit 1
        ;;
esac

case "${manager_network_mode}" in
    public|private|offline|personal_cloud) ;;
    *)
        echo "ERROR: Invalid COMFYUI_MANAGER_NETWORK_MODE: ${manager_network_mode}" >&2
        exit 1
        ;;
esac

"${venv_root}/bin/python" - \
    "${manager_config_file}" \
    "${manager_security_level}" \
    "${manager_network_mode}" <<'PY'
from __future__ import annotations

import configparser
import sys
from pathlib import Path

path = Path(sys.argv[1])
security_level = sys.argv[2]
network_mode = sys.argv[3]

config = configparser.ConfigParser(interpolation=None, strict=False)
if path.exists():
    config.read(path, encoding="utf-8")
if not config.has_section("default"):
    config.add_section("default")
config.set("default", "security_level", security_level)
config.set("default", "network_mode", network_mode)

with path.open("w", encoding="utf-8") as config_file:
    config.write(config_file)
PY

echo "ComfyUI Manager policy: security_level=${manager_security_level}, network_mode=${manager_network_mode}"

image_build_id="$(<"${image_build_id_file}")"
venv_build_id=""
if [[ -f "${venv_build_id_file}" ]]; then
    venv_build_id="$(<"${venv_build_id_file}")"
fi

if [[ "${image_build_id}" != "${venv_build_id}" ]]; then
    echo "Refreshing the persistent Python environment for this ComfyUI image..."
    # shellcheck disable=SC1091
    source /opt/pytorch-install.env
    "${venv_root}/bin/python" -m pip install --upgrade pip setuptools wheel
    "${venv_root}/bin/python" -m pip install \
        "torch==${PYTORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}" \
        --index-url "${TORCH_INDEX_URL}"
    "${venv_root}/bin/python" -m pip install -r "${comfy_root}/requirements.txt"
    if [[ -f "${comfy_root}/manager_requirements.txt" ]]; then
        "${venv_root}/bin/python" -m pip install -r "${comfy_root}/manager_requirements.txt"
    fi
    printf '%s\n' "${image_build_id}" > "${venv_build_id_file}"
fi

manager_enabled="${COMFYUI_MANAGER_ENABLED:-true}"
legacy_manager_ui="${COMFYUI_MANAGER_LEGACY_UI:-false}"
case "${manager_enabled,,}" in
    1|true|yes|on)
        set -- "$@" --enable-manager
        case "${legacy_manager_ui,,}" in
            1|true|yes|on)
                set -- "$@" --enable-manager-legacy-ui
                echo "ComfyUI Manager UI: legacy"
                ;;
            0|false|no|off|"")
                echo "ComfyUI Manager UI: current"
                ;;
            *)
                echo "ERROR: COMFYUI_MANAGER_LEGACY_UI must be true or false." >&2
                exit 1
                ;;
        esac
        ;;
    0|false|no|off|"")
        echo "ComfyUI Manager: disabled"
        ;;
    *)
        echo "ERROR: COMFYUI_MANAGER_ENABLED must be true or false." >&2
        exit 1
        ;;
esac

accelerator="${COMFYUI_ACCELERATOR:-nvidia}"
case "${accelerator,,}" in
    nvidia)
        echo "ComfyUI accelerator: NVIDIA"
        ;;
    cpu)
        set -- "$@" --cpu
        echo "ComfyUI accelerator: CPU"
        ;;
    *)
        echo "ERROR: Unsupported COMFYUI_ACCELERATOR: ${accelerator}" >&2
        exit 1
        ;;
esac

# Persist the effective state of the built/running container into the bind-mounted
# user tree. Backups read this record instead of inferring effective versions from
# .env, which may intentionally contain only local overrides.
"${venv_root}/bin/python" - \
    "${runtime_state_file}" \
    "${build_state_file}" \
    "${image_build_id_file}" \
    "${venv_build_id_file}" \
    "${manager_enabled}" \
    "${legacy_manager_ui}" \
    "${manager_security_level}" \
    "${manager_network_mode}" \
    "${accelerator}" <<'PY'
from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

(
    output_name,
    build_state_name,
    image_build_id_name,
    venv_build_id_name,
    manager_enabled,
    legacy_manager_ui,
    manager_security_level,
    manager_network_mode,
    accelerator,
) = sys.argv[1:]


def read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key] = value
    return values


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def as_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}

build_state = read_key_values(Path(build_state_name))
comfyui_commit = read_text(Path("/opt/comfyui-commit")) or build_state.get("COMFYUI_COMMIT", "unknown")
try:
    comfyui_describe = subprocess.check_output(
        ["git", "-C", "/opt/ComfyUI", "describe", "--tags", "--always", "--dirty"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except (OSError, subprocess.CalledProcessError):
    comfyui_describe = "unknown"

try:
    import torch

    torch_cuda_build = torch.version.cuda or ""
except Exception:
    torch_cuda_build = "unknown"

state = {
    "schema": 1,
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "source": "running-comfyui-container",
    "comfyui": {
        "commit": comfyui_commit,
        "git_describe": comfyui_describe,
        "configured_ref_at_build": build_state.get("COMFYUI_REF", "unknown"),
    },
    "image_build": {
        "build_id": read_text(Path(image_build_id_name)) or build_state.get("COMFYUI_BUILD_ID", "unknown"),
        "base_image": build_state.get("BASE_IMAGE", "unknown"),
        "torch_index_url": build_state.get("TORCH_INDEX_URL", "unknown"),
        "requested_versions": {
            "torch": build_state.get("PYTORCH_BUILD_VERSION", "unknown"),
            "torchvision": build_state.get("TORCHVISION_BUILD_VERSION", "unknown"),
            "torchaudio": build_state.get("TORCHAUDIO_BUILD_VERSION", "unknown"),
        },
    },
    "runtime": {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "torch": package_version("torch"),
        "torchvision": package_version("torchvision"),
        "torchaudio": package_version("torchaudio"),
        "torch_cuda_build": torch_cuda_build,
        "venv_build_id": read_text(Path(venv_build_id_name)) or "unknown",
        "accelerator": accelerator,
        "uid": os.getuid(),
        "gid": os.getgid(),
    },
    "manager": {
        "enabled": as_bool(manager_enabled),
        "legacy_ui": as_bool(legacy_manager_ui),
        "security_level": manager_security_level,
        "network_mode": manager_network_mode,
    },
}

output = Path(output_name)
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    mode="w",
    encoding="utf-8",
    dir=output.parent,
    prefix=f".{output.name}.",
    delete=False,
) as temporary:
    json.dump(state, temporary, indent=2, sort_keys=True)
    temporary.write("\n")
    temporary_name = temporary.name
os.replace(temporary_name, output)
PY

echo "ComfierUI runtime state: ${runtime_state_file}"

if [[ -n "${COMFYUI_EXTRA_ARGS:-}" ]]; then
    mapfile -d '' -t extra_args < <(
        "${venv_root}/bin/python" - <<'PY'
import os
import shlex
import sys

for argument in shlex.split(os.environ.get("COMFYUI_EXTRA_ARGS", "")):
    sys.stdout.buffer.write(argument.encode() + b"\0")
PY
    )
    set -- "$@" "${extra_args[@]}"
fi

echo "ComfyUI commit: $(< /opt/comfyui-commit)"
exec "${venv_root}/bin/python" "${comfy_root}/main.py" "$@"
