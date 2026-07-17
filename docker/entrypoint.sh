#!/usr/bin/env bash
set -Eeuo pipefail

# Keep files created in shared bind mounts writable by the configured group.
umask 0002

readonly comfy_root=/opt/ComfyUI
readonly venv_root=/opt/venv
readonly image_build_id_file=/opt/comfierui-build-id
readonly venv_build_id_file=/opt/venv/.comfierui-build-id
readonly manager_config_file="${comfy_root}/user/__manager/config.ini"

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

legacy_manager_ui="${COMFYUI_MANAGER_LEGACY_UI:-false}"
case "${legacy_manager_ui,,}" in
    1|true|yes|on)
        legacy_arg_present=false
        for arg in "$@"; do
            if [[ "${arg}" == "--enable-manager-legacy-ui" ]]; then
                legacy_arg_present=true
                break
            fi
        done
        if [[ "${legacy_arg_present}" == false ]]; then
            set -- "$@" --enable-manager-legacy-ui
        fi
        echo "ComfyUI Manager UI: legacy"
        ;;
    0|false|no|off|"")
        echo "ComfyUI Manager UI: current"
        ;;
    *)
        echo "ERROR: COMFYUI_MANAGER_LEGACY_UI must be true or false, got: ${legacy_manager_ui}" >&2
        exit 1
        ;;
esac

echo "ComfyUI commit: $(< /opt/comfyui-commit)"
echo "Visible NVIDIA device(s): ${NVIDIA_VISIBLE_DEVICES:-not constrained}"

exec "${venv_root}/bin/python" "${comfy_root}/main.py" "$@"
