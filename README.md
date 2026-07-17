# ComfierUI

A version-pinned Docker Compose deployment for self-hosted ComfyUI. ComfyUI core,
PyTorch, and system libraries live in a built image. Models, workflows, custom
nodes, user state, inputs, outputs, and caches remain persistent outside it.

## What this repository is

- A portable Linux deployment with repository-local storage by default.
- A tested NVIDIA path with automatic GPU and compatibility-profile discovery.
- A CPU configuration for installation, diagnostics, and small workloads.
- A non-root permissions model that also supports existing shared model stores.
- Current built-in ComfyUI Manager, with the legacy interface available by choice.
- Optional external Docker networking, extra model paths, and restic automation.
- Documentation and validation intended to make local changes reviewable.

It is not a universal container for every accelerator. AMD ROCm and Intel GPU
support need their own tested images, devices, and PyTorch packages.

## Requirements

- Linux
- Docker Engine with the Docker Compose v2 plugin
- Git
- Python 3 for the host-side helper scripts
- NVIDIA driver and NVIDIA Container Toolkit for NVIDIA mode
- Sufficient storage for the image, models, custom nodes, and generated assets

## Quick start

Run initialization as the non-root user that should own the persistent files:

```bash
git clone https://github.com/rosstimo/ComfierUI.git
cd ComfierUI
bash scripts/init.sh
$EDITOR .env
bash scripts/preflight.sh
docker compose build --pull comfyui
docker compose up -d
docker compose ps
```

Open `http://127.0.0.1:8188` on the host. For another machine, use an SSH tunnel,
an authenticated reverse proxy, or deliberately change the bind address after
reviewing [networking](docs/NETWORKING.md) and [security](docs/SECURITY.md).

## Accelerator selection

`bash scripts/init.sh` chooses the highest-VRAM NVIDIA GPU when possible:

- CUDA 13 for compute capability 7.5 or newer with a 580+ driver.
- CUDA 12.6 compatibility mode for older NVIDIA architectures or a 525-579
  driver.
- CPU when no supported NVIDIA profile is available.

Explicit modes are also available:

```bash
bash scripts/init.sh auto
bash scripts/init.sh nvidia-cuda13
bash scripts/init.sh nvidia-cuda12
bash scripts/init.sh cpu
```

See [GPU and compatibility](docs/GPU.md) and the
[compatibility matrix](docs/COMPATIBILITY.md).

## New storage or existing assets

Fresh installs use `./data`. Existing installations can reuse their libraries
without copying them:

```dotenv
COMFYUI_MODELS_PATH=/absolute/path/to/models
COMFYUI_WORKFLOWS_PATH=/absolute/path/to/workflows
COMFYUI_SHARED_GID=1234
```

After changing paths:

```bash
bash scripts/check-permissions.sh
bash scripts/preflight.sh --skip-gpu-container-test
```

Compose does not create missing bind paths for you. This prevents accidental
root-owned directories. `scripts/init.sh` creates missing paths as the invoking
user and leaves existing external libraries unchanged.

## Optional existing Docker network

A normal deployment needs only its private project network. To join another
network, such as an AI service stack:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.external-network.yaml
COMFYUI_EXTERNAL_NETWORK=ai-services
```

```bash
docker network create ai-services  # only when it does not already exist
docker compose up -d --force-recreate
```

Other containers on that network use the service DNS name `comfyui` and
container port `8188`.

## Extra model roots

One complete model library through `COMFYUI_MODELS_PATH` is simplest. When model
categories are spread across several roots, copy and edit both examples:

```bash
cp config/extra_model_paths.yaml.example config/extra_model_paths.yaml
cp examples/compose.extra-model-paths.yaml compose.extra-model-paths.yaml
```

Append `compose.extra-model-paths.yaml` to `COMPOSE_FILE`. The paths in the YAML
configuration are container paths and must correspond to bind mounts in the
Compose override.

## Configuration layers

- `compose.yaml`: portable common service, persistence, and safe CPU fallbacks.
- `compose.nvidia.yaml`: NVIDIA image defaults and one selected GPU.
- `compose.cpu.yaml`: explicit CPU image defaults and execution.
- `compose.external-network.yaml`: optional pre-existing network.
- `compose.extra-model-paths.yaml`: optional local file copied from the example.
- `.env`: local values and selected layers.

Run `docker compose config` whenever these layers change.

## Updating

```bash
bash scripts/update.sh
```

That command updates this deployment repository and rebuilds the configured
ComfyUI ref. The default ref is an immutable upstream release tag. Advancing
ComfyUI itself is deliberate: change `COMFYUI_REF`, rebuild, run the release
tests, and retain the old image tag or Git commit for rollback.

ComfyUI core is image-owned and updates through a rebuild. Manager-installed
custom-node code lives in the custom-node bind mount; additional Python packages
live in the `comfyui-python` named volume.

## Backup and restore

The default restic example prioritizes `.env`, active Compose files, custom
nodes, user state, workflows, and inputs. Models, outputs, and the Python volume
are individually optional because they can be very large or reproducible.

```bash
cp config/restic.env.example config/restic.env
cp config/restic-excludes.txt.example config/restic-excludes.txt
chmod 600 config/restic.env
$EDITOR config/restic.env
bash scripts/restic-backup.sh
bash scripts/restic-restore.sh latest /tmp/comfierui-restore
```

See [Backup and restore](docs/BACKUP_RESTORE.md) before enabling timers.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [Compatibility matrix](docs/COMPATIBILITY.md)
- [GPU and accelerator selection](docs/GPU.md)
- [Permissions](docs/PERMISSIONS.md)
- [Networking](docs/NETWORKING.md)
- [Custom nodes and Manager](docs/CUSTOM_NODES.md)
- [Existing-installation migration](docs/MIGRATION.md)
- [Backup and restore](docs/BACKUP_RESTORE.md)
- [Operations and updates](docs/OPERATIONS.md)
- [Testing](docs/TESTING.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security](docs/SECURITY.md)
- [Repository file reference](docs/FILE_REFERENCE.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

## License

A license must be selected before the generalized deployment replaces `main`.
Public visibility alone does not grant broad reuse rights. The release checklist
keeps this as an explicit owner decision rather than silently choosing legal
terms.
