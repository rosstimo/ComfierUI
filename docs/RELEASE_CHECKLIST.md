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
- [ ] Decide whether old Git history also needs a separate credential/history
      rewrite. Cleaning the current tree does not erase earlier commits.

## Deployment tests

- [ ] Test a completely fresh clone and new local `data/` tree.
- [ ] Test NVIDIA CUDA 13 discovery by UUID and actual inference.
- [ ] Test NVIDIA CUDA 12 compatibility mode on appropriate hardware.
- [ ] Test CPU mode.
- [ ] Test an external model library with a supplementary GID.
- [ ] Test current and legacy Manager interfaces.
- [ ] Install a custom node and verify Python-volume persistence across rebuilds.
- [ ] Load, audit, execute, and save output from a migrated workflow.
- [ ] Test optional external-network attachment and peer DNS.
- [ ] Test extra model paths when that example is retained.

## Recovery tests

- [ ] Run a restic backup with the intended source policy.
- [ ] Restore into staging and compare representative files.
- [ ] Test full reconstruction without modifying the live deployment.
- [ ] Test Python-volume restore when that option is enabled.
- [ ] Confirm the restic password has an independent recovery copy.

## Release decision

- [ ] Choose and add a repository license.
- [ ] Decide whether documentation or example assets need separate licensing.
- [ ] Confirm the default `COMFYUI_REF` is an immutable tested tag or commit.
- [ ] Record the tested ComfyUI commit and accelerator compatibility set.
- [ ] Tag the release only after test evidence is reviewed.

## License note

A public repository without a license can be read and forked through GitHub's
platform features, but it does not clearly grant general reuse, modification,
or redistribution rights. A strong-copyleft goal suggests evaluating
AGPL-3.0-or-later, but the owner should make the legal choice explicitly.
