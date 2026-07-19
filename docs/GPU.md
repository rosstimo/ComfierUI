# GPU and accelerator selection

## Support policy

ComfierUI keeps the common deployment accelerator-neutral where practical, but
ships only accelerator profiles that have an actual implementation. Support is
reported in three tiers:

- **Verified:** real hardware and workflow evidence exists.
- **Experimental:** runnable profile exists, but hardware coverage is incomplete.
- **Planned:** no runnable profile is shipped yet.

See [Compatibility](COMPATIBILITY.md) for current evidence.

## Available profiles

1. **NVIDIA CUDA 13, verified:** Turing-or-newer GPUs with a 580+ driver.
2. **NVIDIA CUDA 12.6, experimental:** compatibility mode for Maxwell, Pascal,
   Volta, or hosts with a 525-579 driver.
3. **CPU, experimental:** PyTorch CPU wheels with no accelerator device mapping.

AMD ROCm and Intel GPU acceleration are planned, not implemented. Each requires
a dedicated image, runtime devices, framework packages, Compose override,
preflight logic, and successful workflow testing. They are not silently mapped
to CUDA, and automatic setup currently uses the CPU profile on those systems.

## Discovery

```bash
bash scripts/detect-gpu.sh
bash scripts/init.sh auto
```

NVIDIA discovery lists index, UUID, model, VRAM, driver, and compute capability.
The highest-VRAM NVIDIA GPU is selected by UUID. UUIDs are preferable to numeric
indexes when enumeration can change.

If no configured NVIDIA profile is available, automatic setup selects the
experimental CPU profile and states that AMD and Intel GPU acceleration are not
yet implemented.

## Explicit profile selection

```bash
bash scripts/init.sh nvidia-cuda13
bash scripts/init.sh nvidia-cuda12
bash scripts/init.sh cpu
```

Forced NVIDIA modes reject a host that does not meet the corresponding minimum
rules rather than producing a misleading configuration.

## NVIDIA Container Toolkit preflight

```bash
bash scripts/preflight.sh
```

The preflight runs the selected CUDA base image with the configured GPU. This
proves the Docker runtime path before a large ComfyUI build. To skip only that
container check:

```bash
bash scripts/preflight.sh --skip-gpu-container-test
```

## Adding another accelerator backend

A new backend should be added as a separate profile rather than folded into the
base Compose file. At minimum, it needs:

- a backend-specific Compose override,
- a suitable base image,
- correct device exposure and runtime permissions,
- matching framework packages,
- initialization and preflight detection,
- documentation of unsupported nodes or operators,
- a successful saved-output workflow test.

Only then should the profile move from planned to experimental. It becomes
verified after the compatibility record includes real hardware evidence.

## Compatibility settings

Change these together when creating another tested profile:

```dotenv
COMFYUI_BASE_IMAGE=...
PYTORCH_VERSION=...
TORCHVISION_VERSION=...
TORCHAUDIO_VERSION=...
TORCH_INDEX_URL=...
```

For non-PyTorch backends, introduce clearly named profile variables rather than
reusing CUDA-specific values with a different meaning.

## Multiple GPUs

One service receives one GPU. This reduces accidental VRAM contention and makes
placement explicit. Multiple independent ComfyUI instances should use distinct:

- `COMPOSE_PROJECT_NAME`
- `COMFYUI_PORT`
- `COMFYUI_DATA_PATH`
- `COMFYUI_WORKFLOWS_PATH`
- accelerator device identifier

## CPU mode

CPU mode is useful for setup validation and may run small workflows, but large
diffusion models can be impractical. It is an experimental fallback, not a
performance or broad-compatibility claim.

## Official references

- NVIDIA CUDA release notes: https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/
- NVIDIA CUDA container tags: https://hub.docker.com/r/nvidia/cuda/tags
- PyTorch wheel indexes: https://download.pytorch.org/whl/
- Docker Compose GPU support: https://docs.docker.com/compose/how-tos/gpu-support/
