# Configuration reference

`scripts/init.sh` generates a short local `.env` containing the normal settings
and detected accelerator values. `.env.example` is the comprehensive reference
for advanced overrides. `.env` is ignored by Git.

Run `docker compose config` after every material change.

Rerunning `scripts/init.sh` refreshes detected accelerator and identity values
without deleting optional Compose layers such as an external network. Existing
local choices, including `TZ`, paths, bind address, and port, are preserved.

## Compose selection

| Variable | Purpose |
|---|---|
| `COMPOSE_FILE` | Colon-separated Compose layers, in merge order |
| `COMPOSE_PROJECT_NAME` | Prefix for project resources and service grouping |

Typical values:

```dotenv
# Modern or compatibility NVIDIA profile
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml

# CPU
COMPOSE_FILE=compose.yaml:compose.cpu.yaml

# NVIDIA plus an existing shared network
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.external-network.yaml
COMFYUI_EXTERNAL_NETWORK=ai-services
```

The external network must already exist. The accelerator layer is selected by
initialization; other optional layers are preserved when initialization is run
again.

## Accelerator and image

| Variable | Purpose |
|---|---|
| `COMFYUI_ACCELERATOR` | `nvidia` or `cpu` |
| `COMFYUI_NVIDIA_PROFILE` | Discovery record: `cuda13`, `cuda12`, `none`, or `auto` before initialization |
| `COMFYUI_GPU_DEVICE` | NVIDIA UUID or index granted to the container |
| `NVIDIA_DRIVER_CAPABILITIES` | NVIDIA runtime capabilities; normally `compute,utility` |
| `COMFYUI_IMAGE_REPOSITORY` | Local image repository name |
| `COMFYUI_IMAGE_TAG` | Local image tag |
| `COMFYUI_BASE_IMAGE` | Selected Ubuntu or CUDA base image |
| `COMFYUI_REPO` | Upstream ComfyUI Git repository |
| `COMFYUI_REF` | Branch, tag, or commit to build |
| `PYTORCH_VERSION` | PyTorch version |
| `TORCHVISION_VERSION` | Torchvision version |
| `TORCHAUDIO_VERSION` | Torchaudio version |
| `TORCH_INDEX_URL` | Matching PyTorch wheel index |

Use an immutable release tag or commit for `COMFYUI_REF` when reproducible
rebuilds matter. The default is an upstream release tag; `master` should be a
deliberate test choice.

The CUDA 13, CUDA 12, and CPU profile variables in `.env.example` are inputs to
`scripts/init.sh`. The script writes the selected values into the local `.env` as
`COMFYUI_BASE_IMAGE` and `TORCH_INDEX_URL`.

Treat the base image and PyTorch values as one compatibility set. Changing only
one can produce a successful image build that fails when a workflow executes.

## Identity and permissions

| Variable | Purpose |
|---|---|
| `PUID` | Numeric container user ID, baked into the image |
| `PGID` | Numeric primary group ID, baked into the image |
| `COMFYUI_SHARED_GID` | Supplementary runtime group for shared assets |
| `COMFYUI_UMASK` | Default `0002`, preserving group write access |

Changing `PUID` or `PGID` requires an image rebuild and may require deliberate
ownership repair on bind mounts or the named Python volume.

## Persistent paths

| Variable | Mounted content |
|---|---|
| `COMFYUI_DATA_PATH` | Parent for custom nodes, user, input, output, temp, cache, and home |
| `COMFYUI_MODELS_PATH` | Main model directory |
| `COMFYUI_WORKFLOWS_PATH` | Workflow directory |

Relative paths resolve from the repository. Absolute paths are recommended for
external libraries. All bind sources must exist before `docker compose up`;
initialization creates missing paths without modifying existing external ones.

For multiple model roots, see `config/extra_model_paths.yaml.example` and
`examples/compose.extra-model-paths.yaml`.

## Host network access

| Variable | Purpose |
|---|---|
| `COMFYUI_BIND_ADDRESS` | Host address for the published port; default `127.0.0.1` |
| `COMFYUI_PORT` | Published host port; container port stays 8188 |
| `COMFYUI_EXTERNAL_NETWORK` | Existing Docker network used only by its override |
| `TZ` | Container timezone |

Fresh installations detect the host timezone when possible. Existing `TZ`
settings are not overwritten by later initialization runs.

## Manager

| Variable | Purpose |
|---|---|
| `COMFYUI_MANAGER_ENABLED` | Adds `--enable-manager` |
| `COMFYUI_MANAGER_LEGACY_UI` | Adds the legacy Manager UI flag |
| `COMFYUI_MANAGER_SECURITY_LEVEL` | `strong`, `normal`, `normal-`, or `weak` |
| `COMFYUI_MANAGER_NETWORK_MODE` | `public`, `private`, `offline`, or `personal_cloud` |

The defaults allow registered node-pack installation for a personal self-hosted
instance. Custom nodes are executable code. Do not lower policy settings merely
to make an unexplained installation error disappear.

## Runtime behavior

| Variable | Purpose |
|---|---|
| `COMFYUI_RESTART_POLICY` | Docker restart policy |
| `COMFYUI_STOP_GRACE_PERIOD` | Graceful stop interval |
| `COMFYUI_SHM_SIZE` | Shared memory allocation |
| `COMFYUI_HEALTH_*` | Health-check timing |
| `COMFYUI_EXTRA_ARGS` | Additional ComfyUI CLI arguments parsed with Python `shlex` |

## Shipped examples

- `.env.example`: comprehensive environment-variable reference.
- `config/manager-config.ini.example`: Manager keys written by the entrypoint.
- `config/extra_model_paths.yaml.example`: ComfyUI extra-model-path mapping.
- `examples/compose.extra-model-paths.yaml`: mounts for the matching config.
- `config/restic.env.example`: backup source and retention choices.
- `config/restic-excludes.txt.example`: backup excludes.
- `systemd/*.example`: host backup, maintenance, and deep-check units.
