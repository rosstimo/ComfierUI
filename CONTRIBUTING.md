# Contributing

Keep changes portable, explicit, and testable:

- Do not commit local `.env`, secrets, models, personal workflows, inputs, or
  outputs.
- Put site-specific networks and paths in `.env` or optional Compose layers.
- Treat accelerator families and CUDA generations as separate compatibility
  profiles with evidence.
- Add an example and documentation for every new configuration file.
- Do not let Compose silently create bind sources.
- Explain new system packages, mounts, permissions, and backup consequences.
- Preserve non-root execution unless a change has a documented security reason.
- Run `bash scripts/validate.sh` before submitting changes.
- Include verification commands and rollback notes in nontrivial pull requests.

Hardware-dependent changes should update `docs/COMPATIBILITY.md` and include the
actual GPU, driver, profile, and representative workflow result.
