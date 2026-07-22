# ComfierUI

A Docker Compose setup for people who want to **use ComfyUI without rebuilding
their installation every time an update gets spicy**.

ComfierUI keeps ComfyUI, PyTorch, and system dependencies in a version-pinned
container while keeping your models, workflows, custom nodes, inputs, outputs,
and settings persistent on the host.

The goal is simple:

- easier first-time setup,
- predictable updates,
- fewer dependency pileups,
- straightforward backup and recovery,
- and a clean way to reuse existing model libraries and workflows.

The goal is not to make ComfyUI boring. It is to make **maintaining** ComfyUI
boring.

## Jump to

- [Quick start](#quick-start)
- [Access from another computer](#access-from-another-computer)
- [Reuse existing models and workflows](#reuse-existing-models-and-workflows)
- [Enable automatic backups](#enable-automatic-backups)
- [Join an existing Docker network](#join-an-existing-docker-network)
- [Recovery](#recovery)
- [Setup TL;DR](#setup-tldr)
- [Maintenance TL;DR](#maintenance-tldr)
- [Troubleshooting TL;DR](#troubleshooting-tldr)
- [Documentation](#documentation)

## Quick start

### Requirements

- Linux
- Docker Engine with the Docker Compose v2 plugin
- Git
- Python 3
- NVIDIA driver and NVIDIA Container Toolkit when using an NVIDIA GPU

### Install

Run the setup as the normal user who should own the ComfyUI files.

Clone the repository:

```bash
git clone https://github.com/rosstimo/ComfierUI.git
```

Enter it:

```bash
cd ComfierUI
```

Create the local configuration, detect hardware, and create persistent
directories:

```bash
bash scripts/init.sh
```

Review `.env` before starting. The generated file is intentionally short;
`.env.example` is the comprehensive reference.

```bash
$EDITOR .env
```

Check Docker, Compose, permissions, disk space, and GPU container access:

```bash
bash scripts/preflight.sh
```

Build ComfyUI and the isolated backup helper:

```bash
docker compose build --pull comfyui backup
```

Start it:

```bash
docker compose up -d
```

Confirm the service is healthy:

```bash
docker compose ps
```

A default local-only installation is available at:

```text
http://127.0.0.1:8188
```

`scripts/init.sh` automatically selects a suitable NVIDIA profile when available,
otherwise it configures the experimental CPU profile. It also detects the host
timezone and writes the detected runtime values into `.env`.

## Access from another computer

The safe default binds ComfyUI only to localhost:

```dotenv
COMFYUI_BIND_ADDRESS=127.0.0.1
```

For a trusted LAN or a host protected by an appropriate firewall or authenticated
reverse proxy, set:

```dotenv
COMFYUI_BIND_ADDRESS=0.0.0.0
```

Recreate the service:

```bash
docker compose up -d --force-recreate
```

Then open it from another machine using the server's address, for example:

```text
http://192.168.1.50:8188
```

Binding to `0.0.0.0` exposes the published port on every host interface allowed by
your firewall. Do not expose an unauthenticated ComfyUI instance directly to the
public Internet.

See [Networking](docs/NETWORKING.md) and [Security](docs/SECURITY.md).

## Reuse existing models and workflows

Fresh installations keep the writable model tree at `./data/models`. Manager
installs new models there.

To reuse an existing model library without mixing new downloads into it, add an
absolute path to `.env`:

```dotenv
COMFYUI_EXTRA_MODELS_PATH=/absolute/path/to/existing/models
```

The existing library is mounted read-only. Manager downloads continue going to
the normal writable model tree.

To reuse an existing workflow directory, replace the generated workflow setting
with an absolute path:

```dotenv
COMFYUI_WORKFLOWS_PATH=/absolute/path/to/existing/workflows
```

Do not keep two `COMFYUI_WORKFLOWS_PATH` entries in `.env`.

### Shared-directory permissions

When an existing model or workflow directory uses group permissions, find its
numeric group ID and group name:

```bash
stat -c '%g %G' /absolute/path/to/existing/workflows
```

If the directory is owned by group `1002`, for example, set:

```dotenv
COMFYUI_SHARED_GID=1002
```

One supplementary shared GID can cover multiple external paths when they use the
same group. This does not change host ownership.

After adding or removing `COMFYUI_EXTRA_MODELS_PATH`, rerun initialization so the
extra-model Compose layer stays synchronized:

```bash
bash scripts/init.sh
```

Check access:

```bash
bash scripts/check-permissions.sh
```

Run preflight again:

```bash
bash scripts/preflight.sh
```

If ComfierUI is already running, recreate it after changing paths or shared-group
settings:

```bash
docker compose up -d --force-recreate
```

The setup does not silently create a missing external model-library path as root.
A typo fails instead of leaving a root-owned bind-mount directory behind.

See [Permissions](docs/PERMISSIONS.md), [Migration](docs/MIGRATION.md), and
[Custom nodes, models, and Manager](docs/CUSTOM_NODES.md).

## Enable automatic backups

The normal Compose stack already includes the backup sidecar, but it stays idle
until enabled. Normal users do not need restic, cron, or systemd installed on the
host.

Enable automatic encrypted backups in `.env`:

```dotenv
COMFYUI_BACKUP_ENABLED=true
```

With no other backup overrides, the defaults are:

- backup repository: `./backups`
- first automatic backup: after 10 minutes
- normal interval: every 24 hours
- failed-backup retry: after 60 minutes
- retention: latest 3, daily 7, weekly 4, monthly 12, yearly 3
- included: local deployment configuration, custom nodes, user/Manager state, workflows
- excluded by default: input, output, models, extra models, Python volume
- captured before every snapshot: node-pack Git/Registry state and deterministic source hashes

Models can be enabled or selectively protected when they are private, modified,
gated, obscure, or otherwise difficult to replace. They default off because of
size, not because they are guaranteed to remain downloadable.

If the stack is already running, recreate it after enabling or changing backup
settings:

```bash
docker compose up -d --force-recreate
```

Check backup status:

```bash
bash scripts/backup.sh status
```

Show the manifest captured for the last backup:

```bash
bash scripts/backup.sh state
```

Create a consistent manual snapshot:

```bash
bash scripts/backup.sh now
```

List snapshots:

```bash
bash scripts/backup.sh list
```

Copy `./backups/restic-password` to a separate safe location. A same-disk local
backup does not protect against loss of the entire disk or machine.

The current pre-backup manifest is available at `./backups/state/current.json`.
Restic preserves its history and tags each snapshot with the manifest's capture
ID.

See [Backup and restore](docs/BACKUP_RESTORE.md) for retention overrides,
include/exclude policy, models, offsite options, rollback, and disaster recovery.

## Join an existing Docker network

A normal installation uses its own private Docker network.

To also join an existing network such as `ai-services`, add the external-network
Compose layer to `COMPOSE_FILE` and set the network name in `.env`:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.backup.yaml:compose.external-network.yaml
COMFYUI_EXTERNAL_NETWORK=ai-services
```

Keep your detected accelerator layer and any other optional layers already in
`COMPOSE_FILE`; the example above is illustrative.

The external Docker network must already exist. Verify it with:

```bash
docker network inspect ai-services
```

If the stack is already running, recreate it:

```bash
docker compose up -d --force-recreate
```

Other containers on that network can reach ComfyUI at:

```text
http://comfyui:8188
```

See [Networking](docs/NETWORKING.md).

## Install your first model

ComfierUI uses the familiar legacy Manager interface by default because it
includes server-side model search and installation.

1. Open **Manager** in ComfyUI.
2. Select **Install Models**.
3. Search for a checkpoint suitable for the workflow you want to run.
4. Select **Install** and follow the server logs until it finishes.
5. Refresh the model list or restart ComfyUI if the new model does not appear.

Follow the logs with:

```bash
docker compose logs -f comfyui
```

Manager-installed models go to `COMFYUI_MODELS_PATH`, which defaults to
`./data/models`.

A workflow-template **Download** button may instead download a file through your
web browser. That is usually not useful when ComfyUI runs on a remote server.

The newer Manager interface is available with:

```dotenv
COMFYUI_MANAGER_LEGACY_UI=false
```

Apply the change with:

```bash
docker compose up -d --force-recreate
```

See [Custom nodes, models, and Manager](docs/CUSTOM_NODES.md).

## Recovery

For a functional rollback of the current deployment:

```bash
bash scripts/backup.sh restore SNAPSHOT
```

For disaster recovery into a freshly initialized clone that can access an
existing built-in restic repository:

```bash
bash scripts/recover.sh SNAPSHOT
```

Fresh-clone recovery preserves the recovery host's local hardware, identity,
paths, port, network, and backup choices while reconstructing the recorded Git
commit, exact ComfyUI commit, pinned core runtime, Manager state, custom nodes,
user state, and workflows.

Deliberately excluded models and packages are inventoried and reported rather
than falsely claimed restored.

To extract a snapshot only for manual inspection:

```bash
bash scripts/backup.sh stage SNAPSHOT
```

See [Backup and restore](docs/BACKUP_RESTORE.md) and
[Recovery blueprint](docs/RECOVERY_BLUEPRINT.md).

## Setup TL;DR

Initialize:

```bash
bash scripts/init.sh
```

Review `.env`:

```bash
$EDITOR .env
```

If you added or removed `COMFYUI_EXTRA_MODELS_PATH`, rerun initialization:

```bash
bash scripts/init.sh
```

Preflight:

```bash
bash scripts/preflight.sh
```

Build:

```bash
docker compose build --pull comfyui backup
```

Start:

```bash
docker compose up -d
```

Status:

```bash
docker compose ps
```

Recent logs:

```bash
docker compose logs --tail=100 comfyui
```

### Applying configuration changes

Use `docker compose up -d --force-recreate` after changing runtime settings such
as bind address, port, paths, Manager mode, shared GID, backup settings, Compose
layers, or external-network settings.

```bash
docker compose up -d --force-recreate
```

Rerun `scripts/init.sh` first after adding or removing
`COMFYUI_EXTRA_MODELS_PATH`.

Changes to the image compatibility set, including `COMFYUI_BASE_IMAGE`,
`COMFYUI_REF`, PyTorch-family versions, or `PUID`/`PGID`, require a rebuild before
recreation. Use the update and configuration documentation rather than changing
one compatibility value in isolation.

See [Configuration reference](docs/CONFIGURATION.md).

## Maintenance TL;DR

Start:

```bash
docker compose up -d
```

Status:

```bash
docker compose ps
```

Follow logs:

```bash
docker compose logs -f comfyui
```

Restart ComfyUI without recreating it:

```bash
docker compose restart comfyui
```

Stop ComfyUI without deleting persistent data:

```bash
docker compose stop comfyui
```

Update the repository, rebuild, and recreate the container:

```bash
bash scripts/update.sh
```

Do not casually run `docker compose down -v`. The `-v` removes the persistent
Python environment used by custom nodes.

See [Operations and updates](docs/OPERATIONS.md).

## Troubleshooting TL;DR

Check service state:

```bash
docker compose ps
```

Read recent logs:

```bash
docker compose logs --tail=100 comfyui
```

Rerun the full environment check:

```bash
bash scripts/preflight.sh
```

Check bind-mount permissions:

```bash
bash scripts/check-permissions.sh
```

Use verbose permission details when needed:

```bash
bash scripts/check-permissions.sh --verbose
```

Resolve the Compose configuration without starting anything:

```bash
docker compose config
```

See [Troubleshooting](docs/TROUBLESHOOTING.md).

## Documentation

The README intentionally stays operational and concise. Detailed mechanics live
in the documentation:

- [Configuration reference](docs/CONFIGURATION.md)
- [Operations and updates](docs/OPERATIONS.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Permissions](docs/PERMISSIONS.md)
- [Custom nodes, models, and Manager](docs/CUSTOM_NODES.md)
- [Migration from an existing installation](docs/MIGRATION.md)
- [Networking](docs/NETWORKING.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [Recovery blueprint](docs/RECOVERY_BLUEPRINT.md)
- [Security](docs/SECURITY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [GPU and accelerator selection](docs/GPU.md)
- [Compatibility matrix](docs/COMPATIBILITY.md)
- [Testing](docs/TESTING.md)
- [Repository file reference](docs/FILE_REFERENCE.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

The current verified accelerator path is NVIDIA CUDA 13 on an RTX 4060 Ti.
NVIDIA CUDA 12.6 and CPU profiles are available but experimental. AMD ROCm and
Intel GPU acceleration remain planned until dedicated profiles are implemented
and tested.

The existing Git history is intentionally retained as the record of the project's
evolution. Files removed from the current tree may still exist in older commits,
and any credential ever committed must remain rotated.

## License

ComfierUI is available under the [MIT License](LICENSE). Use it, change it, share
it, or improve it. Keep the copyright and license notice with redistributed
copies.
