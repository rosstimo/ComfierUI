#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi was not found."
    echo "This repository currently provides NVIDIA GPU and CPU profiles."
    exit 1
fi

echo "=== NVIDIA GPUs ==="
nvidia-smi \
    --query-gpu=index,uuid,name,memory.total,driver_version,compute_cap \
    --format=csv

echo
echo "=== Highest-VRAM recommendation ==="
record="$(
    nvidia-smi \
        --query-gpu=uuid,name,memory.total,driver_version,compute_cap \
        --format=csv,noheader,nounits \
    | awk -F', *' 'NF >= 5 {print $3 "\t" $1 "\t" $2 "\t" $4 "\t" $5}' \
    | sort -nr \
    | head -n1
)"

memory="$(cut -f1 <<<"${record}")"
uuid="$(cut -f2 <<<"${record}")"
name="$(cut -f3 <<<"${record}")"
driver="$(cut -f4 <<<"${record}")"
compute="$(cut -f5 <<<"${record}")"

if version_ge "${driver}" 580 && version_ge "${compute}" 7.5; then
    profile=cuda13
elif version_ge "${driver}" 525; then
    profile=cuda12
else
    profile=unsupported
fi

printf 'GPU: %s\nUUID: %s\nVRAM: %s MiB\nDriver: %s\nCompute capability: %s\nRecommended profile: %s\n' \
    "${name}" "${uuid}" "${memory}" "${driver}" "${compute}" "${profile}"
