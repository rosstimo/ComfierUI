# Compatibility matrix

This file distinguishes implemented configuration from verified operation.
Update it when a profile is tested on real hardware.

| Profile | Base image | PyTorch index | Hardware rule | Status |
|---|---|---|---|---|
| NVIDIA CUDA 13 | CUDA 13 / Ubuntu 24.04 | `cu130` | Driver 580+, compute capability 7.5+ | Generalized `v0.28.0` deployment validated on an RTX 4060 Ti with driver 610.43.03 |
| NVIDIA CUDA 12 | CUDA 12.6 / Ubuntu 24.04 | `cu126` | Driver 525+ | Implemented compatibility path; release test required |
| CPU | Ubuntu 24.04 | `cpu` | Linux CPU | Implemented; release test required |
| AMD ROCm | Not provided | Not provided | N/A | Not implemented |
| Intel GPU | Not provided | Not provided | N/A | Not implemented |

The image and wheel indexes support Linux x86_64 and may publish arm64 artifacts,
but the generalized release must not claim an architecture until the complete
image, custom nodes, and representative workflows are tested on it.

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

## Why two NVIDIA profiles

CUDA 13 removed Maxwell, Pascal, and Volta offline compilation and library
support. Those architectures remain on the CUDA 12 compatibility path. CUDA 13
also requires a newer driver series. Initialization examines the highest-VRAM
GPU's compute capability and driver before choosing a profile.

## Recording a test

For each validated host, record:

- host architecture and Linux distribution,
- GPU model and compute capability,
- NVIDIA driver,
- selected base image and PyTorch versions,
- resolved ComfyUI commit,
- one core workflow and one representative custom-node workflow,
- Manager installation and restart persistence,
- backup and staged restore result.

Do not replace compatibility evidence with “latest worked once.”
