#!/usr/bin/env bash
set -Eeuo pipefail

# Keep files created in shared bind mounts writable by the configured group.
umask 0002

readonly comfy_root=/opt/ComfyUI
readonly venv_root=/opt/venv
readonly image_build_id_file=/opt/comfierui-build-id
readonly venv_build_id_file=/opt/venv/.comfierui-build-id

mkdir -p \
    /data/cache/huggingface \
    /data/cache/pip \
    /data/cache/torch \
    /data/home \
    "${comfy_root}/custom_nodes" \
    "${comfy_root}/input" \
    "${comfy_root}/models" \
    "${comfy_root}/output" \
    "${comfy_root}/temp" \
    "${comfy_root}/user/default/workflows"

if [[ ! -x "${venv_root}/bin/python" ]]; then
    echo "ERROR: The persistent Python environment is missing or corrupt." >&2
    echo "Remove the comfyui-python volume, then rebuild the service." >&2
    exit 1
fi

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

echo "ComfyUI commit: $(< /opt/comfyui-commit)"
echo "Visible NVIDIA device(s): ${NVIDIA_VISIBLE_DEVICES:-not constrained}"

exec "${venv_root}/bin/python" "${comfy_root}/main.py" "$@"
