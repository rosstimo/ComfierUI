# GPU and accelerator selection

## Implemented modes

1. NVIDIA CUDA 13 for Turing-or-newer GPUs with a 580+ driver.
2. NVIDIA CUDA 12.6 compatibility mode for Maxwell, Pascal, Volta, or hosts with
   a 525-579 driver.
3. CPU mode using the PyTorch CPU wheel index.

AMD ROCm and Intel GPU support require dedicated Compose layers, images, devices,
and tested PyTorch packages. They are not silently mapped to CUDA or CPU.

## Discovery

```bash
bash scripts/detect-gpu.sh
bash scripts/init.sh auto
```

Discovery lists index, UUID, model, VRAM, driver, and compute capability. The
highest-VRAM GPU is selected by UUID. UUIDs are preferable to numeric indexes
when enumeration can change.

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

## Compatibility settings

Change these together when creating another tested profile:

```dotenv
COMFYUI_BASE_IMAGE=...
PYTORCH_VERSION=...
TORCHVISION_VERSION=...
TORCHAUDIO_VERSION=...
TORCH_INDEX_URL=...
```

The profile values in `.env.example` are defaults, not a promise that every
NVIDIA card or driver can use them.

## Multiple GPUs

One service receives one GPU. This reduces accidental VRAM contention and makes
placement explicit. Multiple independent ComfyUI instances should use distinct:

- `COMPOSE_PROJECT_NAME`
- `COMFYUI_PORT`
- `COMFYUI_DATA_PATH`
- `COMFYUI_WORKFLOWS_PATH`
- GPU UUID

## CPU mode

CPU mode is valuable for setup validation and may run small workflows, but large
diffusion models can be impractical. It is a fallback, not a performance claim.

## Official references

- NVIDIA CUDA release notes: https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/
- NVIDIA CUDA container tags: https://hub.docker.com/r/nvidia/cuda/tags
- PyTorch wheel indexes: https://download.pytorch.org/whl/
- Docker Compose GPU support: https://docs.docker.com/compose/how-tos/gpu-support/
