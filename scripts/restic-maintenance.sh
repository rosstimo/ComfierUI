#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "${repo_root}/scripts/lib.sh"
restic_env="${RESTIC_ENV_FILE:-${repo_root}/config/restic.env}"
mode="${1:-regular}"

if [[ ! -r "${restic_env}" ]]; then
    echo "ERROR: Missing ${restic_env}." >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a
source "${restic_env}"
set +a

: "${RESTIC_REPOSITORY:?Set RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD_FILE:?Set RESTIC_PASSWORD_FILE}"
require_command restic
backup_tag="${RESTIC_TAG:-comfierui}"

restic forget \
    --tag "${backup_tag}" \
    --keep-daily "${RESTIC_KEEP_DAILY:-7}" \
    --keep-weekly "${RESTIC_KEEP_WEEKLY:-5}" \
    --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}" \
    --prune

case "${mode}" in
    regular) restic check --read-data-subset "${RESTIC_CHECK_READ_DATA_SUBSET:-5%}" ;;
    deep) restic check --read-data ;;
    *) echo "Usage: $0 [regular|deep]" >&2; exit 2 ;;
esac
