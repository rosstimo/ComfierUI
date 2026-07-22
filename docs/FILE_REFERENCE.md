# Repository file reference

| Path | Purpose |
|---|---|
| `compose.yaml` | Common service, persistence, health, and runtime environment |
| `compose.nvidia.yaml` | One selected NVIDIA GPU reservation |
| `compose.cpu.yaml` | CPU execution override |
| `compose.external-network.yaml` | Optional existing shared network |
| `.env.example` | Complete local configuration template |
| `Dockerfile` | Reproducible ComfyUI, PyTorch, and system-library image |
| `docker/entrypoint.sh` | Manager policy, venv refresh, and CLI assembly |
| `docker/backup.Dockerfile` | Isolated restic, Git, and state-capture image |
| `docker/capture-state.py` | Atomic deployment and node-pack state capture |
| `docker/backup-entrypoint.sh` | Restic snapshot, locking, state capture, and retention |
| `docker/recovery-blueprint.sh` | Per-snapshot recovery evidence and inventories |
| `scripts/lib.sh` | Shared host-side environment and version helpers |
| `scripts/init.sh` | Fresh setup, paths, identity, and accelerator profile selection |
| `scripts/preflight.sh` | Host, Compose, permission, GPU-runtime, and disk checks |
| `scripts/detect-gpu.sh` | NVIDIA inventory and profile recommendation |
| `scripts/check-permissions.sh` | Numeric container-identity access diagnostics |
| `scripts/update.sh` | Fast-forward, rebuild, and recreate |
| `scripts/validate.sh` | Static shell, Python, YAML, and Compose validation |
| `scripts/audit-workflows.py` | Missing-node and obvious-secret-shape audit |
| `scripts/node-pack-doctor.py` | Read-only failed-import and node-pack behavior report |
| `scripts/restic-backup.sh` | Optional consistent restic backup |
| `scripts/restic-maintenance.sh` | Retention, pruning, and regular/deep checks |
| `scripts/restic-restore.sh` | Restore into a staging directory |
| `config/*.example` | Manager, extra-model-path, and restic templates |
| `examples/compose.extra-model-paths.yaml` | Example extra library mounts |
| `systemd/*.example` | Optional host backup timer templates |
| `.github/workflows/validate.yml` | Pull-request and push static validation |
| `docs/` | Architecture, configuration, migration, operations, and recovery docs |

Runtime-created paths under `data/` and local configuration copies are ignored by
Git.
