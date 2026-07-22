# Architecture

## Separation of concerns

ComfierUI divides the deployment into three kinds of state:

1. **Reproducible application state:** the image supplies ComfyUI core, pinned
   PyTorch packages, system libraries, and core Manager requirements. The
   internal `/opt/ComfyUI` tree is writable at runtime for node compatibility,
   but is recreated from the image with the container.
2. **Portable mutable state:** bind-mounted models, workflows, custom nodes,
   user data, inputs, outputs, caches, and home directory.
3. **Mutable dependency state:** the `comfyui-python` named volume, which starts
   from the image venv and retains packages installed for custom nodes.

This boundary allows core updates by rebuilding the image without deleting user
state or forcing a full custom-node dependency reinstall.

## Compose layers

`compose.yaml` contains the common service definition and safe CPU fallbacks. It
deliberately has no GPU reservation, external network, or site-specific absolute
path.

`compose.nvidia.yaml` supplies NVIDIA image defaults and reserves one selected
GPU. `compose.cpu.yaml` supplies explicit CPU image defaults without a device
reservation. `compose.external-network.yaml`
joins an existing network while retaining the normal project network.

Local deployments may add more override files. `COMPOSE_FILE` in `.env` records
the active order. Later files override or extend earlier files.

## Build flow

1. Docker starts from the configured Ubuntu or NVIDIA CUDA base image.
2. Required host-independent runtime libraries are installed.
3. The configured ComfyUI repository and pinned ref are cloned into `/opt/ComfyUI`.
4. A venv is created with the selected PyTorch compatibility set and ComfyUI
   requirements.
5. The image records a build ID based on the upstream commit and Python package
   choices.
6. At startup, the named venv volume is refreshed only when the image build ID
   changes.
7. The image-owned ComfyUI tree is assigned to the configured runtime identity.
8. ComfyUI starts as that numeric non-root UID/GID.

## Persistent state

| State | Default | Recovery priority |
|---|---|---|
| Models | `data/models` | Optional to critical |
| Custom nodes | `data/custom_nodes` | High |
| User and Manager state | `data/user` | High |
| Workflows | `data/workflows` | High |
| Inputs | `data/input` | User choice |
| Outputs | `data/output` | User choice |
| Cache | `data/cache` | Reproducible |
| Temp | `data/temp` | Disposable |
| Custom-node Python venv | named volume | Optional but useful |

## Runtime compatibility boundary

The non-root ComfyUI identity can write throughout `/opt/ComfyUI`. This mirrors a
normal local installation closely enough for Manager and older node packs that
create legacy frontend files or other application-local artifacts. It does not
grant root, privileged mode, a Docker socket, or access to undeclared host paths.

Bind-mounted custom nodes, user data, models, inputs, outputs, workflows, and the
named Python volume persist. Writes elsewhere in `/opt/ComfyUI` belong to the
container layer and disappear when that container is recreated. ComfierUI core
updates therefore remain image rebuilds through `scripts/update.sh`; Manager may
temporarily change core files, but those changes are not the durable update path.

## Deliberate omissions

- No Docker socket mount.
- No privileged mode.
- No mandatory external network.
- No root container by default.
- No claim that CUDA works for AMD, Intel, or Apple accelerators.
- No personal workflows, models, prompts, or credentials in the repository.
