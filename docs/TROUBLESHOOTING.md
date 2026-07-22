# Troubleshooting

## Start with complete evidence

```bash
docker compose ps
docker compose logs --tail=250 comfyui
docker compose config
```

Use filtered logs only after the complete context is captured.

## Bind source does not exist

Compose deliberately uses `create_host_path: false`. Run `scripts/init.sh` or
correct the path in `.env`; do not let Docker create a root-owned typo directory.

## GPU container fails before ComfyUI builds

1. Run `nvidia-smi`.
2. Run `scripts/detect-gpu.sh`.
3. Confirm the selected CUDA profile matches driver and compute capability.
4. Run `scripts/preflight.sh`.
5. Repair the NVIDIA Container Toolkit before debugging ComfyUI.

CUDA 13 is not the fallback for every NVIDIA card. Maxwell, Pascal, and Volta
belong on the CUDA 12 profile. A modern GPU with a pre-580 driver also selects
CUDA 12 compatibility mode.

## Image builds but inference says no compatible kernel

The wheel may not contain code for that GPU architecture. Re-run initialization
for the CUDA 12 profile or define a tested older compatibility set. Record all
changed image and PyTorch variables together.

## Manager install appears successful but nodes remain missing

Look for policy messages. Registered installs on a remotely accessed personal
instance may require:

```dotenv
COMFYUI_MANAGER_SECURITY_LEVEL=normal
COMFYUI_MANAGER_NETWORK_MODE=personal_cloud
```

Recreate the container and retry one pack before installing a batch.

## Permission denied

Run `scripts/check-permissions.sh`. It evaluates the configured numeric container
identity and parent traversal. Do not recursively chown a shared library until
the intended owner/group model is understood.

## `Failed to fetch ComfyUI`

The internal ComfyUI tree is writable, but the durable core version still comes
from the image. Use `scripts/update.sh` for a persistent core update. A Manager
core update may work in the current container layer and then disappear when the
container is recreated; custom-node installation is independently persistent.

## Hardlink warnings from `uv`

When cache and destination are on different filesystems, `uv` may copy instead
of hardlinking. This is normally a performance warning, not an install failure.

## Missing system library

Custom nodes may load native libraries not required by core. Add a stable
runtime package to the Dockerfile, rebuild, and document which node needs it.
Installing the package only on the host does not change the container.

## Workflow loads but fails validation

Old workflows can contain disconnected preview outputs, changed required inputs,
or branches that were previously bypassed. The dependency audit proves only that
node types exist; it does not rewrite an old graph for current node schemas.

## Workflow runs but no output file appears

Preview nodes do not write to `data/output`. Connect an active core `Save Image`
node to the intended final image branch.

## Auxiliary model download fails

Check outbound access, disk space, cache paths, model-directory write access, and
required authentication. Keep tokens outside workflow JSON whenever the node
supports environment or configuration-based credentials.

## Python venv breaks after a base Python change

The named venv can outlive an image whose Python ABI changed. Back up useful
state, remove only the resolved `comfyui-python` volume, rebuild, and reinstall
custom-node requirements.
