# Permissions

## Fresh installation

Run `scripts/init.sh` as the non-root user that should own persistent data. The
script records that user's numeric UID/GID, creates only missing directories,
and gives newly created directories mode `2775`.

Compose uses `bind.create_host_path: false`. If a source path is misspelled or
missing, startup fails instead of Docker silently creating a root-owned
directory.

The container uses:

- `PUID:PGID` as its primary numeric identity,
- `COMFYUI_SHARED_GID` as a supplementary group,
- umask `0002` so newly created shared files normally retain group write access.

## Existing external libraries

Initialization leaves existing directories and ownership untouched. Diagnose
first:

```bash
bash scripts/check-permissions.sh
```

The check evaluates access using the configured container UID and groups, not
merely the account running the shell. It also detects blocked parent-directory
traversal.

## Shared writable models

```dotenv
COMFYUI_MODELS_PATH=/absolute/path/to/models
COMFYUI_SHARED_GID=1234
```

One common host policy is:

```bash
sudo chgrp -R 1234 /absolute/path/to/models
sudo chmod -R g+rwX /absolute/path/to/models
sudo find /absolute/path/to/models -type d -exec chmod g+s {} +
```

This preserves the owner, grants the shared group access, and causes newly
created files to inherit the directory group. Review before recursive changes.

A read-only model library can still serve generation, but Manager model
downloads and model-management nodes will not be able to write there.

## Why not root

A root container tends to create root-owned host files and gives custom-node
code unnecessary authority. `scripts/init.sh` refuses root initialization unless
`COMFYUI_ALLOW_ROOT_INIT=true` is deliberately exported in the shell.

## Named Python volume

The `comfyui-python` volume is initialized from the image and then mutated by
custom-node dependency installation. A later PUID/PGID change may require volume
ownership repair or intentional recreation.

## Runtime checks

```bash
docker compose exec comfyui id
docker compose exec comfyui sh -lc \
  'test -w /opt/ComfyUI/custom_nodes && echo custom_nodes-writable'
docker compose exec comfyui sh -lc \
  'test -r /opt/ComfyUI/models && echo models-readable'
```

A Manager security-policy error is not a filesystem permission error. Always
read the complete log before changing ownership.

## Official reference

- Compose bind mounts and `create_host_path`: https://docs.docker.com/reference/compose-file/services/#long-syntax-5
