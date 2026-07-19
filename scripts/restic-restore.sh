#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "${repo_root}/scripts/lib.sh"
restic_env="${RESTIC_ENV_FILE:-${repo_root}/config/restic.env}"

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 SNAPSHOT [TARGET]" >&2
    echo "Example: $0 latest /tmp/comfierui-restore" >&2
    exit 2
fi
if [[ ! -r "${restic_env}" ]]; then
    echo "ERROR: Missing ${restic_env}." >&2
    exit 1
fi

snapshot="$1"
target="${2:-/tmp/comfierui-restore}"

# shellcheck disable=SC1090
set -a
source "${restic_env}"
set +a

: "${RESTIC_REPOSITORY:?Set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD_FILE:?Set RESTIC_PASSWORD_FILE}"
require_command restic
require_command python3
backup_tag="${RESTIC_TAG:-comfierui}"

if [[ "${snapshot}" == latest ]]; then
    snapshot="$(
        restic snapshots --tag "${backup_tag}" --json \
        | python3 -c '
import json
import sys
from datetime import datetime

snapshots = json.load(sys.stdin)
if not snapshots:
    raise SystemExit("no snapshots matched the configured tag")

def timestamp(item):
    return datetime.fromisoformat(item["time"].replace("Z", "+00:00"))

print(max(snapshots, key=timestamp)["id"])
'
    )"
    echo "Resolved latest snapshot tagged ${backup_tag}: ${snapshot}"
fi

if [[ -e "${target}" && ! -d "${target}" ]]; then
    echo "ERROR: Restore target exists and is not a directory: ${target}" >&2
    exit 1
fi
if [[ -d "${target}" ]] && find "${target}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo "ERROR: Restore target is not empty: ${target}" >&2
    echo "Use a new or empty staging directory so restored data cannot mix with older files." >&2
    exit 1
fi

mkdir -p "${target}"
restic restore "${snapshot}" --target "${target}"

echo "Restored to staging directory: ${target}"
echo "Inspect paths, ownership, workflows, and image metadata before copying anything into the live deployment."
echo "Restored workflows and ComfyUI images may contain credentials or other private metadata."
