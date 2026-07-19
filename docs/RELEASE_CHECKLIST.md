# Release checklist

Before replacing `main` with the generalized deployment:

## Repository and documentation

- [ ] Review the fresh generalized tree and confirm legacy setup scripts,
      submodules, personal workflows, and private assets are absent.
- [x] Review `compose.yaml` and shipped overrides with `docker compose config`.
- [x] Confirm shipped configuration examples and documentation cover the supported
      deployment paths.
- [x] Confirm static GitHub Actions validation passes.
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
- [x] Install custom nodes and verify Python-volume persistence across rebuilds.
- [x] Load, audit, execute, and save output from migrated workflows.
- [x] Test optional external-network attachment and peer DNS.
- [x] Test extra/legacy model paths through the shipped extra-model override.

Unverified profiles remain labeled **experimental**. AMD and Intel GPU
acceleration remain **planned** until a dedicated Compose profile and real
workflow evidence exist; they are not blockers for the verified NVIDIA release.

## Recovery tests

- [x] Run encrypted built-in restic backups with the intended default source policy.
- [x] Test manual consistent snapshots and retention behavior.
- [x] Restore into staging and inspect representative files.
- [x] Test a functional live rollback of custom nodes, user state, workflows, and
      deployment-local configuration.
- [x] Confirm excluded model/image categories remain untouched during live rollback.
- [x] Test selective include restore without deleting unbacked sibling files.
- [x] Confirm tracked repository source remains Git-authoritative during live restore.
- [x] Capture recovery blueprint state from the actual running container.
- [x] Test full fresh-clone reconstruction with `scripts/recover.sh`.
- [x] Confirm the recorded ComfierUI and ComfyUI commits are reconstructed.
- [x] Confirm the pinned PyTorch CUDA 13 core environment is reconstructed.
- [x] Restore custom nodes/workflows/user state and reconcile custom-node requirements.
- [x] Confirm recovered Python dependencies pass `pip check`.
- [x] Confirm excluded models are inventoried/reported rather than falsely restored.
- [x] Confirm fresh recovery uses an isolated image tag and cannot replace another
      deployment's generic local image tag.
- [ ] Test complete Python-volume payload restore when that option is enabled.
- [ ] Confirm the production restic password has an independent recovery copy.

## Release decision

- [x] Add the MIT License for repository code and documentation.
- [x] Confirm the default `COMFYUI_REF` is an immutable tested tag or commit.
- [x] Record the tested ComfyUI commit and verified accelerator compatibility set.
- [ ] Complete final current-tree privacy/secret review.
- [ ] Tag a release after the generalized deployment is merged to `main` and the
      clean `main` Quick Start is re-tested.

## Compatibility policy

A profile is **verified** only after successful real-hardware startup, model
loading, workflow execution, and saved output are documented. Runnable but
unproven profiles are **experimental**. Backends without a runnable profile are
**planned**. This lets the project evolve toward AMD, Intel, and other backends
without presenting speculative configurations as supported.
