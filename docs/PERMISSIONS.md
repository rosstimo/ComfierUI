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
- `COMFYUI_SHARED_GID` as one supplementary numeric group,
- umask `0002` so newly created shared files normally retain group write access.

Inside the container, that non-root identity owns `/opt/ComfyUI`. This permits
the same application-local writes that many ordinary desktop installations
permit, including legacy node-pack frontend installers. The ownership change is
inside the image and does not broaden the host mounts.

## Existing external libraries

Initialization leaves existing directories and ownership untouched. Diagnose
access first:

```bash
bash scripts/check-permissions.sh
```

The normal output is intentionally compact. For owner, group, mode, and full path
details, use:

```bash
bash scripts/check-permissions.sh --verbose
```

The check evaluates access using the configured container UID and groups, not
merely the account running the shell. It also detects blocked parent-directory
traversal.

When an inaccessible path already grants the needed permissions to its owning
group, the checker suggests the numeric `COMFYUI_SHARED_GID` and host group name.
This is usually safer than recursively changing an existing shared library.

You can inspect a path's numeric GID and group name directly with:

```bash
stat -c '%g %G' /absolute/path/to/shared/directory
```

For example, output such as:

```text
1002 ComfyUI
```

maps directly to:

```dotenv
COMFYUI_SHARED_GID=1002
```

One supplementary shared GID can cover multiple model and workflow paths when
they use the same group.

After changing `COMFYUI_SHARED_GID`, recreate running containers:

```bash
docker compose up -d --force-recreate
```

## Shared writable models

For an intentionally writable shared model tree, first identify the existing
shared group's numeric GID and configure it rather than inventing a placeholder
number:

```dotenv
COMFYUI_MODELS_PATH=/absolute/path/to/models
COMFYUI_SHARED_GID=1002
```

When changing host permissions is actually desired, a typical group-based policy
is:

```bash
sudo chgrp -R 1002 /absolute/path/to/models
```

```bash
sudo chmod -R g+rwX /absolute/path/to/models
```

```bash
sudo find /absolute/path/to/models -type d -exec chmod g+s {} +
```

Replace `1002` with the real group selected for that library. These commands
preserve the owner, grant the shared group access, and cause newly created files
to inherit the directory group. Review paths before recursive changes.

A read-only extra model library can still serve generation. ComfierUI's standard
extra-model mount is intentionally read-only even when the underlying host group
has write permission. Manager downloads continue going to the normal writable
`COMFYUI_MODELS_PATH` tree.

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
```

```bash
docker compose exec comfyui sh -lc \
  'test -w /opt/ComfyUI/custom_nodes && echo custom_nodes-writable'
```

```bash
docker compose exec comfyui sh -lc \
  'test -w /opt/ComfyUI && echo application-tree-writable'
```

```bash
docker compose exec comfyui sh -lc \
  'test -r /opt/ComfyUI/models && echo models-readable'
```

A Manager security-policy error is not a filesystem permission error. Always
read the complete log before changing ownership.

## Official reference

- Compose bind mounts and `create_host_path`: https://docs.docker.com/reference/compose-file/services/#long-syntax-5
