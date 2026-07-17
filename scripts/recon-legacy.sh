#!/usr/bin/env bash
set -Eeuo pipefail

legacy_root="${1:-/mnt/nvme2/ComfierUI}"

[[ -d "${legacy_root}" ]] || {
    echo "ERROR: Legacy root does not exist: ${legacy_root}" >&2
    exit 1
}

printf 'Legacy root: %s\n\n' "${legacy_root}"

for relative_path in \
    ComfyUI/models \
    ComfyUI/custom_nodes \
    ComfyUI/user \
    ComfyUI/input \
    ComfyUI/output \
    models \
    custom_nodes \
    user \
    input \
    output \
    workflows
do
    path="${legacy_root}/${relative_path}"
    [[ -e "${path}" || -L "${path}" ]] || continue

    printf '%s\n' "--- ${relative_path} ---"
    stat -c 'type=%F owner=%U:%G mode=%a path=%n' "${path}"
    if [[ -L "${path}" ]]; then
        printf 'link=%s\n' "$(readlink "${path}")"
        printf 'resolved=%s\n' "$(readlink -f "${path}")"
    fi
    du -shL "${path}" 2>/dev/null || true
    printf '\n'
done
