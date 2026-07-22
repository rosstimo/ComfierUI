# Recovery blueprint

ComfierUI backups separate **unique recovery data** from **reproducible state**.

The goal is not to copy every byte by default. The goal is to preserve the small state that is difficult to recreate, record the exact known-good runtime around it, and keep enough information to rebuild the rest safely.

> Back up what is unique. Rebuild what is reproducible. Inventory everything needed to return to the known-good state.

## Recovery payload

The default encrypted backup stores the small state that is difficult or annoying to recreate:

- local deployment configuration,
- custom-node code,
- ComfyUI and Manager user state,
- workflows.

Models, input/output images, extra model libraries, and the persistent Python volume remain opt-in because they can be very large.

Models are still legitimate backup candidates. Public availability can disappear, accounts can be lost, repositories can become gated or private, and locally modified files may never be reproducible. Size is the reason models default off, not an assumption that every model is disposable.

## Recovery blueprint

Every built-in snapshot also stores a small recovery blueprint describing the known state at backup time, including state that may not be present in the payload itself.

The blueprint contains:

- ComfierUI repository commit when detectable,
- actual running ComfyUI commit,
- configured ComfyUI ref used at build time,
- image build identity and base image,
- requested PyTorch, TorchVision, and TorchAudio versions,
- actual installed PyTorch-family versions,
- Python version,
- CUDA build reported by PyTorch when applicable,
- accelerator type,
- Manager behavior settings,
- backup include/exclude coverage,
- effective fine-grained include and exclude policy,
- custom-node directory names, enabled state, installation source, exact Git
  state or Registry version, and deterministic source-content hashes,
- installed Python package names and versions,
- writable model-library filenames and sizes,
- extra/legacy model-library filenames and sizes when configured,
- input filenames and sizes,
- output filenames and sizes.

The running ComfyUI container writes its effective build/runtime state into the
persistent user tree. Immediately before each restic snapshot, the backup service
also atomically captures the deployment and all installed node packs. Recovery
therefore uses observed state instead of inferring effective versions from
`.env` or a floating `latest` label.

## Blueprint files

A snapshot recovery blueprint contains files such as:

```text
state.env
current-state.json
container-state.json
backup-includes.txt
backup-excludes.txt
custom-nodes.tsv
python-packages.tsv
models.tsv
extra-models.tsv
input-files.tsv
output-files.tsv
README.txt
```

The snapshot also contains `/backups/state/current.json`; the blueprint copy is
named `current-state.json`. It records a unique capture ID, deployment Git state,
Compose-file hashes, the running-container state, and one structured entry for
each installed node pack.

For Git packs, the entry includes the full commit, branch or detached state,
tags, sanitized origin URL, dirty status and changed-file list, submodules, and a
source-content hash. Git hashes cover tracked and unignored files; archive and
manual-pack hashes exclude runtime caches such as `__pycache__`, `.venv`, and
`node_modules`. Registry packages are recognized by Manager's `.tracking` file and
their `pyproject.toml` ID/version. Manual directories and single-file nodes still
receive a content hash.

The selected Manager channel is recorded only when installation evidence makes
it knowable. In particular, an unpacked Registry package proves the installed
version but usually cannot prove whether the user selected that version or
selected `latest`.

`container-state.json` is the detailed machine-readable record of the running
core environment.

The `*.tsv` inventories are recovery roadmaps. They are not proof that the corresponding payload was backed up.

Model files are not hashed by default. Hashing a very large model library would require reading every byte and could turn a lightweight backup into an expensive full-disk verification job.

Custom-node Git remotes are recorded for provenance, but HTTP(S) user information
is removed first. The capture adds a warning when it redacts such a remote.

## Portable state versus host-local state

A recovery should reproduce application state without assuming the recovery host is identical to the original machine.

### Portable recovery state

Portable state includes:

- ComfierUI repository commit,
- ComfyUI commit,
- PyTorch-family version pins,
- Manager settings,
- custom-node state,
- workflows,
- ComfyUI/Manager user state,
- backed-up optional data.

### Host-local state

Host-local state should normally be detected or configured on the recovery host:

- Compose project identity,
- GPU UUID and accelerator selection,
- UID/GID and supplementary group IDs,
- bind address and port,
- absolute host paths,
- external Docker network names,
- backup destination policy.

Fresh recovery preserves these local choices instead of blindly copying the old host's `.env` over the new one.

## Git is authoritative for tracked project source

A restic snapshot may contain tracked Dockerfiles, Compose files, scripts, and documentation for staging, inspection, or disaster-recovery evidence.

Those copies are **not** the authority when recovering tracked project source.

The recorded `repository_commit` is authoritative. Fresh recovery checks out that Git commit. Live rollback keeps the currently checked-out tracked source and restores only local deployment configuration such as `.env`.

This prevents an old restic snapshot from silently overlaying stale tracked files onto a newer checkout.

## Live rollback versus fresh-clone recovery

ComfierUI has two recovery workflows.

### Existing deployment rollback

```bash
bash scripts/backup.sh restore SNAPSHOT
```

Use this when an existing deployment needs to be rolled back in place.

It creates a pre-restore safety snapshot, restores backed-up live data categories, restores local `.env` configuration, optionally restores the Python volume, rebuilds when needed, and restarts the deployment.

### Fresh-clone disaster recovery

```bash
bash scripts/recover.sh SNAPSHOT
```

Use this when reconstructing a freshly initialized clone from an existing built-in restic repository.

## Fresh-clone recovery flow

The intended workflow is:

1. Clone ComfierUI.
2. Run `scripts/init.sh` so the recovery host detects its own hardware, identity, paths, and networking defaults.
3. Make the encrypted restic repository and password available through `COMFYUI_BACKUP_PATH`.
4. Ensure `compose.backup.yaml` is included in `COMPOSE_FILE`.
5. Start only the backup service.
6. List snapshots and choose an explicit known-good snapshot ID.
7. Run `scripts/recover.sh SNAPSHOT`.

Example:

```bash
bash scripts/init.sh
$EDITOR .env

docker compose up -d backup
bash scripts/backup.sh list
bash scripts/recover.sh a1b2c3d4
```

The fresh recovery helper then:

1. stages the selected snapshot,
2. reads the recovery blueprint,
3. preserves host-local `.env` choices,
4. stops and removes the recovery clone's backup containers before volume surgery,
5. removes a fresh empty Python volume when necessary,
6. checks out the recorded ComfierUI repository commit in detached-HEAD mode,
7. imports portable ComfyUI/PyTorch/Manager pins from the blueprint,
8. restores custom nodes, user/Manager state, workflows, and backed-up optional categories,
9. rebuilds the pinned core image,
10. restores a complete Python-volume payload when it is safe to reuse,
11. otherwise rebuilds the Python environment,
12. starts ComfyUI and waits for health,
13. installs discovered custom-node `requirements.txt` files,
14. runs `pip check`,
15. restarts and rechecks ComfyUI,
16. restarts the backup service,
17. reports Python and model inventory differences.

## Exact version reconstruction

Fresh recovery prefers the exact ComfyUI commit reported by the running container over the configured build tag when both are available.

For PyTorch-family packages, the image build's requested versions are used as rebuild inputs. Runtime strings such as `2.11.0+cu130` are recorded for evidence but are not blindly reused as pip request strings.

The recovery host's accelerator/base-image/Torch-index settings remain authoritative when the snapshot accelerator differs from the recovery host.

## Python recovery strategy

The default Python volume is not backed up.

The reproducible core environment is rebuilt from pinned image inputs. Restored custom nodes are then reconciled through their `requirements.txt` files and checked with `pip check`.

The blueprint's `python-packages.tsv` inventory is used for comparison rather than blindly reinstalling the entire historical environment.

When a complete Python-volume payload exists and the snapshot accelerator matches the recovery host accelerator, fresh recovery can reuse the backed-up volume.

Selective individual Python-volume include paths are intentionally not applied automatically during fresh recovery. The core environment is rebuilt and differences are reported instead.

## Models and excluded assets

Model inventories record relative paths and sizes even when models are excluded from the payload.

After fresh recovery, ComfierUI compares the inventory with the configured model paths and reports how many inventoried files are present, along with a sample of missing paths.

It does not automatically redownload missing models and does not claim excluded files were restored.

Important or irreplaceable assets should either be included in the built-in backup or protected by an independent backup system.

## Recovery image isolation

Fresh recovery may run on the same Docker host as another ComfierUI deployment.

To avoid replacing a shared `local/comfierui:latest` tag during a recovery build, `scripts/recover.sh` automatically assigns a recovery-specific `COMFYUI_IMAGE_TAG` when the setting is otherwise absent. An explicitly configured image tag is preserved.

This keeps a recovery build from changing what another deployment would use on its next container recreation.

## What the blueprint cannot guarantee

An inventory entry proves that an item existed when the snapshot was created. It does not prove that an excluded file is recoverable.

Likewise, a recorded custom-node commit may no longer exist upstream. The default backup therefore stores custom-node code itself while using commit information as additional evidence.

The blueprint is a roadmap connecting Git, the container build, the encrypted recovery payload, and separately managed large assets. It is not a substitute for backing up data that cannot be recreated.
