# Release checklist

Before replacing `main` with the generalized deployment:

## Repository and documentation

- [ ] Review the fresh generalized tree and confirm legacy setup scripts,
      submodules, personal workflows, and private assets are absent.
- [ ] Review `compose.yaml` and every override with `docker compose config`.
- [ ] Confirm every shipped config file has an example and documentation.
- [ ] Confirm static GitHub Actions validation passes.
- [ ] Search the full current tree for private paths, model names, prompts,
      credentials, tokens, and generated files.
- [x] Retain the existing Git history as the record of the project's evolution.
- [x] Document that removed files remain visible in earlier commits and exposed
      credentials must remain rotated.

## Deployment tests

- [x] Test a completely fresh clone and new local `data/` tree.
- [x] Test NVIDIA CUDA 13 discovery by UUID and actual inference.
- [ ] Test NVIDIA CUDA 12 compatibility mode on appropriate hardware.
- [ ] Test CPU mode with a representative saved-output workflow.
- [x] Test an external model library with a supplementary GID.
- [x] Test current and legacy Manager interfaces.
- [x] Install a custom node and verify Python-volume persistence across rebuilds.
- [x] Load, audit, execute, and save output from a migrated workflow.
- [x] Test optional external-network attachment and peer DNS.
- [ ] Test extra model paths when that example is retained.

Unverified profiles remain labeled **experimental**. AMD and Intel GPU
acceleration remain **planned** until a dedicated Compose profile and real
workflow evidence exist; they are not blockers for the verified NVIDIA release.

## Recovery tests

- [ ] Run a restic backup with the intended source policy.
- [ ] Restore into staging and compare representative files.
- [ ] Test full reconstruction without modifying the live deployment.
- [ ] Test Python-volume restore when that option is enabled.
- [ ] Confirm the restic password has an independent recovery copy.

## Release decision

- [x] Add the MIT License for repository code and documentation.
- [x] Confirm the default `COMFYUI_REF` is an immutable tested tag or commit.
- [x] Record the tested ComfyUI commit and verified accelerator compatibility set.
- [ ] Tag the release only after the desired recovery checks are reviewed.

## Compatibility policy

A profile is **verified** only after successful real-hardware startup, model
loading, workflow execution, and saved output are documented. Runnable but
unproven profiles are **experimental**. Backends without a runnable profile are
**planned**. This lets the project evolve toward AMD, Intel, and other backends
without presenting speculative configurations as supported.
