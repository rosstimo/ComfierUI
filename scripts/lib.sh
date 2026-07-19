#!/usr/bin/env bash
# Shared host-side helpers. This file is sourced by other scripts.

# Restore data should inherit the destination host's ownership and timestamps
# instead of trying to recreate host-local metadata from the staging tree.
# Preserve normal file modes so executable scripts remain executable. Scope this
# cp wrapper to backup.sh only; other host-side scripts keep normal cp behavior.
#
# A restic snapshot may contain tracked Docker/Compose project files for staging
# and disaster-recovery inspection, but live restore must not overwrite the
# currently checked-out Git tree with older copies. Git is authoritative for
# tracked project files. When backup.sh overlays /source/repo, restore only the
# deployment-local .env; the recorded repository commit remains the roadmap for
# recovering tracked source on a fresh clone.
if [[ "${BASH_SOURCE[1]:-}" == */backup.sh ]]; then
    cp() {
        local source_operand=""
        local destination_operand=""

        if (( $# >= 2 )); then
            source_operand="${@: -2:1}"
            destination_operand="${@: -1}"
        fi

        if [[ "${source_operand}" == */source/repo/. ]] && \
           [[ "${destination_operand%/}" == "${repo_root%/}" ]]; then
            local staged_repo="${source_operand%/.}"
            if [[ -f "${staged_repo}/.env" ]]; then
                command cp \
                    "${staged_repo}/.env" \
                    "${repo_root}/.env" \
                    --no-preserve=ownership,timestamps,xattr,context
            fi
            return 0
        fi

        command cp "$@" --no-preserve=ownership,timestamps,xattr,context
    }
fi

bool_true() {
    case "${1,,}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: Required command not found: $1" >&2
        return 1
    }
}

env_get() {
    local key="$1"
    local fallback="${2-}"
    local env_file="${3:-.env}"
    local value=""

    if [[ -r "${env_file}" ]]; then
        value="$(sed -n "s/^${key}=//p" "${env_file}" | tail -n1 | tr -d '\r')"
    fi

    if [[ ${#value} -ge 2 ]]; then
        if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] || \
           [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
            value="${value:1:${#value}-2}"
        fi
    fi

    printf '%s\n' "${value:-${fallback}}"
}

env_set() {
    local key="$1"
    local value="$2"
    local env_file="${3:-.env}"

    python3 - "${env_file}" "${key}" "${value}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
replacement = f"{key}={value}"
updated = []
replaced = False
for line in lines:
    if line.startswith(f"{key}="):
        if not replaced:
            updated.append(replacement)
            replaced = True
    else:
        updated.append(line)
if not replaced:
    if updated and updated[-1] != "":
        updated.append("")
    updated.append(replacement)
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
}

version_ge() {
    local actual="$1"
    local minimum="$2"
    [[ "$(printf '%s\n%s\n' "${minimum}" "${actual}" | sort -V | head -n1)" == "${minimum}" ]]
}

resolve_host_path() {
    local path="$1"
    local base="${2:-$PWD}"
    python3 - "${path}" "${base}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1]).expanduser()
base = Path(sys.argv[2])
if not path.is_absolute():
    path = base / path
print(path.resolve(strict=False))
PY
}
