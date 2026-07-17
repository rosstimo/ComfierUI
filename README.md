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
- straightforward backup and restore,
- and a clean way to reuse an existing model library.

The goal is not to make ComfyUI boring. It is to make **maintaining** ComfyUI
boring.

## Quick start

### Requirements

- Linux
- Docker Engine with the Docker Compose v2 plugin
- Git
- Python 3
- NVIDIA driver and NVIDIA Container Toolkit when using an NVIDIA GPU

### Install

Run this as the normal user who should own the ComfyUI files:

```bash
git clone https://github.com/rosstimo/ComfierUI.git
cd ComfierUI

bash scripts/init.sh
$EDITOR .env
bash scripts/preflight.sh

docker compose build --pull comfyui
docker compose up -d
```

Open:

```text
http://127.0.0.1:8188
```

That is the normal setup. `scripts/init.sh` detects a suitable NVIDIA profile
when available and otherwise configures CPU mode.

## Setup TL;DR

```bash
# Create .env, detect hardware, and create persistent directories
bash scripts/init.sh

# Check Docker, permissions, storage, Compose, and GPU access
bash scripts/preflight.sh

# Build and start
docker compose build --pull comfyui
docker compose up -d

# Confirm it is alive
docker compose ps
docker compose logs --tail=100 comfyui
```

## Maintenance TL;DR

```bash
# Start
docker compose up -d

# Status
docker compose ps

# Follow logs
docker compose logs -f comfyui

# Restart ComfyUI
docker compose restart comfyui

# Stop without deleting persistent data
docker compose stop comfyui

# Update this repo, rebuild, and recreate the container
bash scripts/update.sh
```

Do not casually run `docker compose down -v`. The `-v` removes the persistent
Python environment used by custom nodes.

## Why updates are less painful

ComfyUI core is built into the image at a pinned release. Updating means building
a new image rather than modifying the running installation in place.

Your important state stays outside the image:

- models,
- workflows,
- custom nodes,
- ComfyUI and Manager settings,
- inputs and outputs,
- caches,
- custom-node Python packages.

That separation makes rollback, rebuilding, and troubleshooting much less
mysterious. Custom nodes can still break things because custom nodes are tiny
Python roommates with opinions, but the damage is easier to isolate.

## Use existing models and workflows

Fresh installs store everything under `./data`.

To reuse an existing library, edit `.env`:

```dotenv
COMFYUI_MODELS_PATH=/absolute/path/to/models
COMFYUI_WORKFLOWS_PATH=/absolute/path/to/workflows
COMFYUI_SHARED_GID=1234
```

Then check access before starting:

```bash
bash scripts/check-permissions.sh
bash scripts/preflight.sh --skip-gpu-container-test
```

The setup will not silently create missing bind-mount paths as root. A typo fails
loudly instead of leaving a weird permissions souvenir.

See [Permissions](docs/PERMISSIONS.md) and
[Migration](docs/MIGRATION.md) for existing installations.

## ComfyUI Manager and custom nodes

The current built-in Manager interface is enabled by default. Custom-node code
and its Python dependencies persist across normal image rebuilds.

After installing or updating nodes:

```bash
docker compose restart comfyui
docker compose logs --since=5m comfyui
```

See [Custom nodes and Manager](docs/CUSTOM_NODES.md) for policy settings,
dependency repair, and clean venv recovery.

## Optional shared Docker network

A normal installation uses its own private Docker network.

To join an existing network such as `ai-services`, edit `.env`:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.external-network.yaml
COMFYUI_EXTERNAL_NETWORK=ai-services
```

Other containers on that network can reach ComfyUI at:

```text
http://comfyui:8188
```

See [Networking](docs/NETWORKING.md).

## Backup and restore

Restic examples are included for configuration, workflows, custom nodes, user
state, and optionally models, outputs, and the Python environment.

```bash
cp config/restic.env.example config/restic.env
cp config/restic-excludes.txt.example config/restic-excludes.txt
chmod 600 config/restic.env
$EDITOR config/restic.env

bash scripts/restic-backup.sh
bash scripts/restic-restore.sh latest /tmp/comfierui-restore
```

Read [Backup and restore](docs/BACKUP_RESTORE.md) before enabling the included
systemd timers.

## Documentation

Start here when the quick commands are not enough:

- [Configuration reference](docs/CONFIGURATION.md)
- [Operations and updates](docs/OPERATIONS.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Permissions](docs/PERMISSIONS.md)
- [Custom nodes and Manager](docs/CUSTOM_NODES.md)
- [Migration from an existing installation](docs/MIGRATION.md)
- [Networking](docs/NETWORKING.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [Security](docs/SECURITY.md)

<details>
<summary><strong>Nerd stuff: architecture, hardware support, and project validation</strong></summary>

### How it is put together

The common Compose configuration handles persistence, permissions, networking,
health checks, and Manager state. Accelerator-specific settings live in separate
Compose overrides.

- NVIDIA CUDA 13 is currently verified on an RTX 4060 Ti.
- NVIDIA CUDA 12.6 and CPU profiles are available but still considered
  experimental until broader hardware testing is recorded.
- AMD ROCm and Intel GPU profiles are not shipped yet. They need dedicated
  images, device mappings, framework packages, and real workflow tests.

Automatic setup uses a supported NVIDIA profile when detected. On an AMD- or
Intel-GPU machine, it currently falls back to CPU rather than pretending GPU
acceleration works.

More detail:

- [Architecture](docs/ARCHITECTURE.md)
- [GPU and accelerator selection](docs/GPU.md)
- [Compatibility matrix](docs/COMPATIBILITY.md)
- [Testing](docs/TESTING.md)
- [Repository file reference](docs/FILE_REFERENCE.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

The existing Git history is intentionally retained because this project has
changed substantially over time. Files removed from the current tree may still
exist in older commits, and any credential ever committed must remain rotated.

</details>

## License

ComfierUI is available under the [MIT License](LICENSE). Use it, change it, share
it, or improve it. Keep the copyright and license notice with redistributed
copies.
