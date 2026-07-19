#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

for script in docker/*.sh scripts/*.sh; do
    bash -n "${script}"
done
echo "PASS  shell syntax"

python3 -m py_compile scripts/audit-workflows.py
rm -rf scripts/__pycache__
echo "PASS  Python syntax"

work_env="$(mktemp)"
trap 'rm -f "${work_env}"' EXIT
cp .env.example "${work_env}"

validate_compose() {
    local label="$1"
    shift
    docker compose --env-file "${work_env}" "$@" config >/dev/null
    echo "PASS  ${label}"
}

validate_compose "base Compose" -f compose.yaml
validate_compose "NVIDIA Compose" -f compose.yaml -f compose.nvidia.yaml
validate_compose "CPU Compose" -f compose.yaml -f compose.cpu.yaml
validate_compose "built-in backup Compose" \
    -f compose.yaml -f compose.nvidia.yaml -f compose.backup.yaml
COMFYUI_EXTERNAL_NETWORK=validation-network \
    validate_compose "external-network Compose" \
        -f compose.yaml -f compose.nvidia.yaml -f compose.external-network.yaml
COMFYUI_EXTRA_MODELS_PATH=/tmp/comfierui-validation-models \
    validate_compose "external-model-library Compose" \
        -f compose.yaml -f compose.nvidia.yaml -f compose.extra-models.yaml
COMFYUI_EXTRA_MODELS_PATH=/tmp/comfierui-validation-models \
COMFYUI_BACKUP_PATH=/tmp/comfierui-validation-backups \
    validate_compose "backup with extra-model inventory Compose" \
        -f compose.yaml -f compose.nvidia.yaml -f compose.extra-models.yaml \
        -f compose.backup.yaml
validate_compose "extra-model-paths example" \
    -f compose.yaml -f compose.nvidia.yaml -f examples/compose.extra-model-paths.yaml

# Compose validates all deployment YAML above. When PyYAML happens to be
# available, also parse the non-Compose YAML examples. It is intentionally not
# a normal host dependency.
if python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 - <<'PY'
from pathlib import Path
import yaml

for path in (
    Path("config/extra_model_paths.yaml"),
    Path("config/extra_model_paths.yaml.example"),
    Path(".github/workflows/validate.yml"),
):
    yaml.safe_load(path.read_text(encoding="utf-8"))
print("PASS  supplemental YAML parse")
PY
else
    echo "SKIP  supplemental YAML parse (PyYAML not installed)"
fi

if find . \( -type d -name __pycache__ -o -type f -name '*.pyc' \) -print -quit | grep -q .; then
    echo "FAIL  Python cache artifacts found" >&2
    exit 1
fi

echo "Validation passed."
