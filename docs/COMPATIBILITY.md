# Compatibility matrix

This repository separates **verified**, **experimental**, and **planned**
accelerator support. A profile is not called supported merely because a base
image or package repository exists.

## Support levels

- **Verified:** built and exercised on real hardware with successful ComfyUI
  startup, model loading, workflow execution, and saved output.
- **Experimental:** runnable configuration is implemented and statically
  validated, but representative hardware testing is incomplete.
- **Planned:** backend is recognized, but no runnable Compose profile is shipped.

| Profile | Tier | Base image | Framework source | Status |
|---|---|---|---|---|
| NVIDIA CUDA 13 | Verified | CUDA 13 / Ubuntu 24.04 | PyTorch `cu130` | Validated on RTX 4060 Ti with driver 610.43.03 |
| NVIDIA CUDA 12.6 | Experimental | CUDA 12.6 / Ubuntu 24.04 | PyTorch `cu126` | Implemented compatibility path; hardware validation pending |
| CPU | Experimental | Ubuntu 24.04 | PyTorch CPU wheels | Implemented fallback; representative workflow validation pending |
| AMD ROCm | Planned | Not provided | Not selected | No image, device mapping, or Compose profile shipped |
| Intel GPU | Planned | Not provided | Not selected | No image, device mapping, or Compose profile shipped |

The common Compose design is accelerator-neutral where practical: persistence,
permissions, networking, Manager state, and backups do not depend on the GPU
vendor. Accelerator-specific images, devices, environment variables, and
framework packages belong in separate overrides.

## Verified test records

### NVIDIA CUDA 13, July 17, 2026

- Host: Linux x86_64
- GPU: NVIDIA GeForce RTX 4060 Ti, 16 GB
- Driver: 610.43.03
- Profile selected: NVIDIA CUDA 13
- ComfyUI ref: `v0.28.0`
- PyTorch: 2.11.0 from the `cu130` wheel index
- Existing model library: mounted through the configured shared path and GID
- Manager and custom nodes: installed, restarted, and imported successfully
- Networking: isolated review deployment served through host port 8190
- Inference: test image generated successfully in the UI

Port 8190 was only an isolated review override. The generic deployment continues
to use ComfyUI's standard host port 8188 by default.

## Planned test targets

The following systems are intended as future compatibility tests rather than
release blockers:

- Framework laptop
- GPD Win Max 2

For each system, record the actual host architecture, operating system, CPU,
graphics hardware, available runtime, selected accelerator profile, and
representative workflow result. Do not infer Intel or AMD acceleration support
merely from a product name. Until a dedicated GPU profile is implemented, these
systems use the experimental CPU profile.

## Promotion criteria

Promote a profile from experimental to verified only after documenting:

- host architecture and Linux distribution,
- exact accelerator and driver/runtime version,
- base image and framework package versions,
- resolved ComfyUI commit,
- preflight and container health,
- one core workflow with saved output,
- one representative custom-node workflow when practical,
- restart and rebuild persistence,
- any backend-specific limitations.

Adding AMD or Intel GPU acceleration requires more than documentation. It needs a
separate Compose override, suitable image, device exposure, framework packages,
preflight checks, and a real test record.

## NVIDIA profile split

CUDA 13 removed Maxwell, Pascal, and Volta offline compilation and library
support. Those architectures remain on the CUDA 12 compatibility path. CUDA 13
also requires a newer driver series. Initialization examines the highest-VRAM
NVIDIA GPU's compute capability and driver before choosing a profile.

Do not replace compatibility evidence with “latest worked once.”
