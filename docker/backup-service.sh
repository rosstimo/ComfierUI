#!/bin/sh
set -eu

case "${COMFYUI_BACKUP_ENABLED:-false}" in
    1|true|TRUE|yes|YES|on|ON)
        exec /bin/sh /usr/local/bin/comfierui-backup daemon
        ;;
    *)
        printf '%s\n' "Built-in backups are disabled. Set COMFYUI_BACKUP_ENABLED=true to enable automatic encrypted backups."
        exec sleep infinity
        ;;
esac
