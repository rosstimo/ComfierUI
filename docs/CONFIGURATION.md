# Configuration reference

`scripts/init.sh` generates a short local `.env` containing the normal settings
and detected accelerator values. `.env.example` is the comprehensive reference
for advanced overrides. `.env` is ignored by Git.

Run `docker compose config` after every material change.

Rerunning `scripts/init.sh` refreshes detected accelerator and identity values
without deleting unrelated optional Compose layers such as an external network.
Existing local choices, including `TZ`, paths, bind address, port, and backup
enablement, are preserved. When `COMFYUI_EXTRA_MODELS_PATH` is set,
initialization automatically enables `compose.extra-models.yaml`; removing the
setting and rerunning init removes that layer.

## Applying configuration changes

Different changes need different levels of action:

| Change | Action |
|---|---|
| `COMFYUI_EXTRA_MODELS_PATH` added or removed | rerun `scripts/init.sh`, then preflight and recreate |
| bind address, port, paths, Manager mode, `COMFYUI_SHARED_GID`, backup settings, Compose layers, external network | `docker compose up -d --force-recreate` |
| `COMFYUI_BASE_IMAGE`, `COMFYUI_REF`, PyTorch-family versions | rebuild image, then recreate |
| `PUID` or `PGID` | rebuild image, recreate, and review bind/volume ownership |
| documentation-only or backup retention values before the backup service has ever started | no image rebuild; recreate running affected services when applicable |

After path, permission, network, or Compose-layer changes, rerun:

```bash
bash scripts/preflight.sh
```

For a normal runtime-only configuration change, apply it with:

```bash
docker compose up -d --force-recreate
```

Do not change one member of the image compatibility set casually. Base image,
ComfyUI revision, PyTorch-family versions, and wheel index are tested together.

## Compose selection

| Variable | Purpose |
|---|---|
| `COMPOSE_FILE` | Colon-separated Compose layers, in merge order |
| `COMPOSE_PROJECT_NAME` | Prefix for project resources and service grouping |

Typical values:

```dotenv
# Modern or compatibility NVIDIA profile
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.backup.yaml

# CPU
COMPOSE_FILE=compose.yaml:compose.cpu.yaml:compose.backup.yaml

# NVIDIA plus a read-only existing model library
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.backup.yaml:compose.extra-models.yaml
COMFYUI_EXTRA_MODELS_PATH=/absolute/path/to/existing/models

# NVIDIA plus an existing shared network
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.backup.yaml:compose.external-network.yaml
COMFYUI_EXTERNAL_NETWORK=ai-services
```

The backup layer is included in normal generated configuration but remains idle
unless `COMFYUI_BACKUP_ENABLED=true`.

The external network must already exist. The accelerator layer is selected by
initialization. Other optional layers are preserved when initialization is run
again, except `compose.extra-models.yaml`, which is synchronized with whether
`COMFYUI_EXTRA_MODELS_PATH` is configured.

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

Use `COMFYUI_SHARED_GID` when an existing shared workflow or model directory is
already group-owned and the container should use that group's permissions without
changing host ownership.

Find the real numeric GID and group name with:

```bash
stat -c '%g %G' /absolute/path/to/shared/directory
```

For output such as `1002 ComfyUI`, configure:

```dotenv
COMFYUI_SHARED_GID=1002
```

`scripts/check-permissions.sh` also suggests a usable shared GID when the path's
existing group permissions would solve an access failure.

Changing `PUID` or `PGID` requires an image rebuild and may require deliberate
ownership repair on bind mounts or the named Python volume. Changing
`COMFYUI_SHARED_GID` changes supplementary group access at runtime and does not
change host ownership by itself; recreate running containers to apply it.

## Persistent paths

| Variable | Mounted content |
|---|---|
| `COMFYUI_DATA_PATH` | Parent for custom nodes, user, input, output, temp, cache, and home |
| `COMFYUI_MODELS_PATH` | Main writable model directory; default `./data/models` |
| `COMFYUI_EXTRA_MODELS_PATH` | Optional existing model library mounted read-only and registered as extra paths |
| `COMFYUI_WORKFLOWS_PATH` | Workflow directory |

Relative paths resolve from the repository. Absolute paths are recommended for
external libraries. All bind sources must exist before `docker compose up`.
Initialization creates missing repository-local paths but does not create or
modify the external model library itself.

To reuse an existing workflow directory, replace the generated value rather than
adding a second duplicate key:

```dotenv
COMFYUI_WORKFLOWS_PATH=/absolute/path/to/existing/workflows
```

### Existing or legacy model library

Keep `COMFYUI_MODELS_PATH` as the normal writable model tree. Manager downloads
and manually added new models go there.

To expose an existing library separately, add this to `.env`:

```dotenv
COMFYUI_EXTRA_MODELS_PATH=/absolute/path/to/existing/models
```

Then rerun:

```bash
bash scripts/init.sh
```

```bash
bash scripts/preflight.sh
```

Initialization adds `compose.extra-models.yaml` to `COMPOSE_FILE` and pre-creates
`COMFYUI_MODELS_PATH/external` as the target for the nested read-only bind mount.
Inside the container, the external library appears at:

```text
/opt/ComfyUI/models/external
```

`config/extra_model_paths.yaml` registers the standard model categories from
that tree. The external library remains read-only, while Manager downloads keep
using the normal writable `/opt/ComfyUI/models` tree backed by
`COMFYUI_MODELS_PATH`.

Custom-node-specific model directories are not guessed automatically. Add
additional mappings only when the relevant node pack documents and requires
them.

For more complex layouts with several independent model roots, use
`config/extra_model_paths.yaml.example` and
`examples/compose.extra-model-paths.yaml` as a starting point.

## Host network access

| Variable | Purpose |
|---|---|
| `COMFYUI_BIND_ADDRESS` | Host address for the published port; default `127.0.0.1` |
| `COMFYUI_PORT` | Published host port; container port stays 8188 |
| `COMFYUI_EXTERNAL_NETWORK` | Existing Docker network used only by its override |
| `TZ` | Container timezone |

Fresh installations detect the host timezone when possible. Existing `TZ`
settings are not overwritten by later initialization runs.

`127.0.0.1` is the safer default because only the Docker host can access the
published port. For a trusted LAN or a protected server, set:

```dotenv
COMFYUI_BIND_ADDRESS=0.0.0.0
```

Then recreate the container and browse to `http://<server-ip>:8188` from another
machine. Binding to `0.0.0.0` exposes the port on every host interface allowed by
your firewall, so do not publish an unauthenticated ComfyUI service directly to
the public Internet.

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

## Shipped examples and configuration

- `.env.example`: comprehensive environment-variable reference.
- `compose.extra-models.yaml`: optional read-only existing-library mount.
- `config/extra_model_paths.yaml`: standard category mapping used by that mount.
- `config/manager-config.ini.example`: Manager keys written by the entrypoint.
- `config/extra_model_paths.yaml.example`: customizable multi-root model mapping.
- `examples/compose.extra-model-paths.yaml`: mounts for the customizable example.
- `config/restic.env.example`: advanced host-managed backup source and retention choices.
- `config/restic-excludes.txt.example`: advanced host-managed backup excludes.
- `systemd/*.example`: advanced host backup, maintenance, and deep-check units.
