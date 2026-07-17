# Release checklist

Before replacing `main` with the generalized deployment:

## Repository and documentation

- [ ] Review the fresh generalized tree and confirm legacy setup scripts,
      submodules, personal workflows, and private assets are absent.
- [ ] Review `compose.yaml` and every override with `docker compose config`.
- [ ] Confirm every shipped config file has an example and documentation.
- [x] Confirm static GitHub Actions validation passes.
- [ ] Search the full current tree for private paths, model names, prompts,
      credentials, tokens, and generated files.
- [x] Preserve the existing Git history as the record of the project's evolution.
      Removed files remain visible in older commits; previously exposed
      credentials must remain rotated and invalid.

## Deployment tests

- [x] Test a completely fresh clone and new local `data/` tree.
- [x] Test NVIDIA CUDA 13 discovery by UUID and actual inference.
- [ ] Test NVIDIA CUDA 12 compatibility mode on appropriate hardware.
- [ ] Test CPU mode.
- [x] Test an external model library with a supplementary GID.
- [x] Test current and legacy Manager interfaces.
- [x] Install a custom node and verify Python-volume persistence across rebuilds.
- [x] Load, audit, execute, and save output from a migrated workflow.
- [x] Test optional external-network attachment and peer DNS.
- [ ] Test extra model paths when that example is retained.
- [ ] Record future Framework laptop test results.
- [ ] Record future GPD Win Max 2 test results.

## Recovery tests

- [ ] Run a restic backup with the intended source policy.
- [ ] Restore into staging and compare representative files.
- [ ] Test full reconstruction without modifying the live deployment.
- [ ] Test Python-volume restore when that option is enabled.
- [ ] Confirm the restic password has an independent recovery copy.

## Release decision

- [x] License repository code and documentation under the MIT License.
- [x] Preserve the existing repository history.
- [x] Confirm the default `COMFYUI_REF` is an immutable tested tag or commit.
- [x] Record the tested ComfyUI commit and accelerator compatibility set.
- [ ] Tag the release only after the intended recovery tests are reviewed.

## Release philosophy

ComfierUI is a convenience project for people running ComfyUI. The MIT License
allows broad use, modification, redistribution, sublicensing, and commercial use
while requiring preservation of the copyright and license notice.

The repository history is intentionally retained. The earlier host-based setup,
workflow experiments, and Docker overhaul document how the project evolved.
Removing files from the current tree does not erase them from earlier commits,
so any credential ever committed must remain rotated rather than being treated
as removed merely because it no longer appears on the current branch.
