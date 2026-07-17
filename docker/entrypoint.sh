#!/usr/bin/env bash
set -Eeuo pipefail

comfy_root=/opt/ComfyUI
venv_root=/opt/venv
manager_config_file="${comfy_root}/user/__manager/config.ini"
image_build_id_file=/opt/comfierui-build-id
venv_build_id_file=/opt/venv/.comfierui-build-id

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
case "${manager_enabled,,}" in
    1|true|yes|on)
        set -- "$@" --enable-manager
        legacy_manager_ui="${COMFYUI_MANAGER_LEGACY_UI:-false}"
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
