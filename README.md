# ComfierUI

A reproducible NVIDIA Docker Compose deployment for ComfyUI with persistent models, custom nodes, user state, workflows, inputs, outputs, caches, and custom-node Python dependencies.

This branch replaces the old host virtual environment, ComfyUI submodule, separate ComfyUI-Manager submodule, launch script, and systemd service workflow.

## What this deployment does

- Builds ComfyUI directly from the official upstream repository.
- Installs CUDA-enabled PyTorch inside the image.
- Installs the Manager dependencies supplied by ComfyUI.
- Starts ComfyUI with `--enable-manager`.
- Uses the current Manager interface by default, with the legacy interface available as an option.
- Pins the container to a selected NVIDIA GPU UUID.
- Runs ComfyUI as the invoking host user's UID and GID.
- Keeps runtime data outside the image under `data/`.
- Keeps repository workflows under version control in `workflows/`.
- Uses host port `8189` by default so it can be tested beside a legacy service on `8188`.

## Requirements

- Linux
- Docker Engine
- Docker Compose plugin
- NVIDIA driver
- NVIDIA Container Toolkit
- Git

Confirm GPU access before continuing:

```bash
docker run --rm --gpus all \
    nvidia/cuda:13.0.0-base-ubuntu24.04 \
    nvidia-smi
```

## Initial deployment

Clone this branch into a new directory rather than replacing a working legacy installation in place:

```bash
cd /mnt/nvme2

git clone \
    --branch docker-compose \
    https://github.com/rosstimo/ComfierUI.git \
    ComfierUI-docker

cd ComfierUI-docker
bash scripts/init.sh
```

The initialization script:

1. Creates the persistent directory structure.
2. Copies `.env.example` to `.env` when needed.
3. Selects the detected NVIDIA GPU with the most VRAM.
4. Records the invoking user's UID and GID.
5. Validates the resolved Compose configuration.

Review the generated configuration:

```bash
cat .env
docker compose config
```

Build and start ComfyUI:

```bash
docker compose build --pull comfyui
docker compose up -d
docker compose ps
docker compose logs -f comfyui
```

The default test URL is:

```text
http://SERVER-IP:8189
```

## Persistent layout

```text
ComfierUI-docker/
├── compose.yaml
├── Dockerfile
├── docker/
│   └── entrypoint.sh
├── scripts/
├── workflows/                 # tracked by Git
└── data/                      # ignored by Git
    ├── cache/
    ├── custom_nodes/
    ├── home/
    ├── input/
    ├── models/
    ├── output/
    ├── temp/
    └── user/
```

The named volume `comfierui_comfyui-python` preserves Python packages installed for custom nodes through ComfyUI-Manager. Image rebuilds refresh the pinned PyTorch packages and current ComfyUI requirements while retaining additional custom-node packages.

## GPU selection

`scripts/init.sh` selects the GPU with the most VRAM. To choose another GPU, edit `.env` and set its UUID:

```dotenv
COMFYUI_GPU_DEVICE=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

List available UUIDs with:

```bash
nvidia-smi --query-gpu=index,uuid,name,memory.total --format=csv
```

Use the UUID rather than a numeric index so device selection remains stable if enumeration order changes.

## Manager interface mode

The current ComfyUI Manager interface is the default:

```dotenv
COMFYUI_MANAGER_LEGACY_UI=false
```

To temporarily use the familiar legacy Manager interface, set:

```dotenv
COMFYUI_MANAGER_LEGACY_UI=true
```

Apply a mode change without rebuilding the image:

```bash
docker compose up -d --force-recreate comfyui
```

The startup log reports either `ComfyUI Manager UI: current` or `ComfyUI Manager UI: legacy`.

## Legacy installation reconnaissance

Before migrating anything, inspect the old layout and resolve symlinks:

```bash
bash scripts/recon-legacy.sh /mnt/nvme2/ComfierUI
```

Do not blindly copy the old virtual environment, ComfyUI source tree, Manager source tree, caches, or every custom node. Bring up the clean container first, then migrate selected assets in this order:

1. Models
2. Workflows
3. Required input images
4. Selected output images
5. Custom nodes needed by tested workflows
6. User settings only when still applicable

Use `rsync -aHAX --info=progress2` for large asset migrations after confirming the real source paths.

## Common operations

```bash
# Status
docker compose ps

# Follow logs
docker compose logs -f comfyui

# Restart
docker compose restart comfyui

# Stop without deleting persistent data
docker compose down

# Update this branch, rebuild, and restart
bash scripts/update.sh

# Open a shell in the running container
docker compose exec comfyui bash

# Show the exact upstream ComfyUI commit in the image
docker compose exec comfyui cat /opt/comfyui-commit
```

## Rebuilding the Python volume

Normally the Python volume should be retained because it contains custom-node dependencies. To intentionally rebuild it from the image:

```bash
docker compose down
docker volume rm comfierui_comfyui-python
docker compose up -d --build
```

Removing that volume does not remove models, workflows, inputs, outputs, or user data stored under `data/`.

## Security notes

- Do not commit `.env`, API keys, access tokens, model-site credentials, or private workflows.
- Workflow JSON can contain credentials entered into node widgets. Inspect workflows before committing them.
- ComfyUI is bound to all host interfaces by default. Restrict `COMFYUI_BIND_ADDRESS`, firewall the port, or place it behind an authenticated reverse proxy before exposing it beyond a trusted network.
- Custom nodes execute code inside the container and can modify mounted data. Install only nodes you trust.

## Legacy files

The old submodules, systemd unit, and host setup scripts remain in the branch temporarily for migration review. They are not used by `compose.yaml` and can be removed after the Docker deployment and selected workflow migrations are verified.
