# Backup and restore

ComfierUI includes a Docker Compose managed backup service for normal installations and keeps host-managed or offsite backup workflows as an advanced option.

The recovery model is intentionally simple:

> Back up what is unique. Rebuild what is reproducible. Inventory everything needed to return to a known-good state.

There are two different recovery operations:

- `scripts/backup.sh restore` rolls an existing live deployment back to a snapshot.
- `scripts/recover.sh` reconstructs a freshly initialized clone from a snapshot.

They are deliberately separate because a same-host rollback and a fresh-host disaster recovery have different safety requirements.

## Built-in automatic backups

The normal backup path is fully Docker Compose managed:

- no host restic installation,
- no host cron job,
- no systemd timer,
- no Docker socket mounted into the backup container.

The backup service uses restic and writes an encrypted repository to the host-visible backup directory. The default layout is:

```text
./backups/
├── restic/           # encrypted restic repository
├── restore/          # optional plaintext staged snapshot copies
├── state/            # scheduler state plus current.json and recovery blueprint
└── restic-password   # generated repository password
```

The entire `./backups/` tree is ignored by Git.

A local backup directory on the same disk is useful for rollback, accidental deletion, and bad updates. It does not protect against loss of the disk or machine. Important installations should also keep an independent copy on another disk, system, or offsite destination.

## Enable backups

For a fresh installation, the backup sidecar is already part of the normal Compose configuration but remains idle until enabled.

Set:

```dotenv
COMFYUI_BACKUP_ENABLED=true
```

Then recreate the services:

```bash
docker compose up -d --force-recreate
```

For an older deployment that does not already include the backup layer, add `compose.backup.yaml` to `COMPOSE_FILE` once. Keep only the layers the deployment actually uses.

Example:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.extra-models.yaml:compose.backup.yaml
COMFYUI_BACKUP_ENABLED=true
```

On startup, the backup helper creates the required backup directory and persistent Python volume with the configured host identity. The backup service then:

1. generates `restic-password` when one does not already exist,
2. initializes the encrypted restic repository when needed,
3. waits for the configured initial delay,
4. creates backups automatically on the configured interval,
5. applies retention after successful scheduled backups.

Copy the generated restic password somewhere safe and independent if the backups matter. A restic repository without its password cannot be recovered.

## Easy backup configuration

The normal settings live in `.env`:

```dotenv
# Turn automatic backups on or off.
COMFYUI_BACKUP_ENABLED=true

# Host-visible backup directory.
COMFYUI_BACKUP_PATH=./backups

# First automatic backup after 10 minutes, then every 24 hours.
COMFYUI_BACKUP_START_DELAY_MINUTES=10
COMFYUI_BACKUP_INTERVAL_HOURS=24

# Retry a failed automatic backup after one hour.
COMFYUI_BACKUP_RETRY_MINUTES=60

# Manual backups wait this long for an in-progress ComfyUI startup.
COMFYUI_BACKUP_READY_TIMEOUT_SECONDS=300
```

## What is included

The default backup protects the small state that is difficult or annoying to recreate:

```dotenv
COMFYUI_BACKUP_INCLUDE_CONFIG=true
COMFYUI_BACKUP_INCLUDE_CUSTOM_NODES=true
COMFYUI_BACKUP_INCLUDE_USER=true
COMFYUI_BACKUP_INCLUDE_WORKFLOWS=true
```

Potentially large categories are opt-in:

```dotenv
COMFYUI_BACKUP_INCLUDE_INPUT=false
COMFYUI_BACKUP_INCLUDE_OUTPUT=false
COMFYUI_BACKUP_INCLUDE_MODELS=false
COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS=false
COMFYUI_BACKUP_INCLUDE_PYTHON=false
```

The configuration category includes `.env` plus tracked deployment files needed for staging, inspection, and disaster-recovery evidence. Git remains authoritative for tracked repository source during live rollback and fresh-clone recovery.

User state includes ComfyUI and Manager state. Workflows are backed up from `COMFYUI_WORKFLOWS_PATH`, including an external workflow directory.

Inputs and outputs default to off because image collections can be large and generated images may embed workflow metadata. The Python volume defaults to off because the pinned core environment is normally rebuilt and custom-node dependencies can be reconciled separately.

Models default to off because they often dominate backup size, not because they are guaranteed to remain downloadable forever. A model that is public today may later become private, gated, renamed, deleted, or tied to account access that is no longer available. Private, modified, obscure, or otherwise difficult-to-replace models should be backed up here or protected independently.

## Fine-grained include and exclude policy

The broad `COMFYUI_BACKUP_INCLUDE_*` switches are the normal interface, but the backup can be made much smaller or much larger.

Optional local policy files:

```text
config/backup-includes.txt
config/backup-excludes.txt
```

Both are ignored by Git. Tracked `.example` files document the syntax.

Point `.env` at local copies:

```dotenv
COMFYUI_BACKUP_INCLUDE_FILE=./config/backup-includes.txt
COMFYUI_BACKUP_EXCLUDE_FILE=./config/backup-excludes.txt
```

Useful roots inside the backup container are:

```text
/source/repo
/source/data
/source/workflows
/source/models
/source/extra-models
/source/python
```

The include file adds exact files or subtrees. For example, keep broad model backup disabled while protecting only hard-to-replace assets:

```text
/source/models/checkpoints/rare-model.safetensors
/source/models/loras/private-collection
/source/extra-models/checkpoints/unavailable
```

Or enable a full category and exclude replaceable content:

```dotenv
COMFYUI_BACKUP_INCLUDE_MODELS=true
COMFYUI_BACKUP_EXCLUDE_FILE=./config/backup-excludes.txt
```

Example exclusion:

```text
/source/models/checkpoints/easily-redownloaded/**
```

An optional global size ceiling can prevent unexpectedly large files from entering the payload:

```dotenv
COMFYUI_BACKUP_EXCLUDE_LARGER_THAN=20G
```

Leave it empty for no file-size ceiling. Do not set a ceiling when the point of the backup is to preserve large model files.

Custom include paths are restricted to `/source/...` so the backup cannot accidentally include its own restic repository. The effective include and exclude policy is copied into every recovery blueprint.

A snapshot created with an exclusion pattern or file-size ceiling is treated as potentially partial during restore. ComfierUI overlays backed-up content instead of deleting unbacked sibling files.

## Recovery blueprint

Every backup also records a small recovery blueprint. It describes the known state even when large payload categories were intentionally excluded.

The blueprint records information such as:

- ComfierUI repository commit,
- actual running ComfyUI commit,
- requested and installed PyTorch-family versions,
- Python version,
- base image and build identity,
- Manager settings,
- backup coverage and fine-grained policy,
- every installed node pack's directory, enabled state, install type, exact Git
  commit and dirty state when available, Registry ID/version when detectable,
  and deterministic source-content hash,
- installed Python package inventory,
- writable and extra-model file inventories,
- input and output file inventories.

Immediately before restic starts reading files, the backup process atomically
replaces `backups/state/current.json`. Registry installs record their installed
version. Git/nightly installs record their full commit, branch, tags, sanitized
remote, dirty-file list, and submodule state. Every pack also gets a deterministic
source-content hash, which covers archive/manual installs that have no `.git`.

Manager does not always retain whether a Registry version was chosen explicitly
or arrived through `latest`. The manifest records that selection as unknown
rather than guessing. An installed Git checkout with Manager's CNR marker can be
identified as `nightly`.

The state capture ID is also added to the restic snapshot as a
`state:CAPTURE_ID` tag. A shared backup lock prevents a scheduled backup and a
manual backup from capturing or writing snapshots simultaneously.

The running ComfyUI container writes authoritative runtime/build state. The
recovery blueprint consumes that state instead of guessing effective versions
from `.env`. If a manual backup begins while ComfyUI is still starting, the host
helper waits for the health check before stopping it. State capture also warns
when it finds a runtime record from an older schema after an image update.

Inventories are a roadmap, not proof that excluded files are stored in restic. See [Recovery blueprint](RECOVERY_BLUEPRINT.md).

## Retention

Default retention:

```dotenv
COMFYUI_BACKUP_KEEP_LAST=3
COMFYUI_BACKUP_KEEP_DAILY=7
COMFYUI_BACKUP_KEEP_WEEKLY=4
COMFYUI_BACKUP_KEEP_MONTHLY=12
COMFYUI_BACKUP_KEEP_YEARLY=3
```

Retention first selects snapshots by the stable configured backup tag, then
groups them by restic host. The unique `state:CAPTURE_ID` tag therefore does not
split every snapshot into its own retention group.

Successful scheduled backups and `maintenance` apply retention and pruning.

Manual backups and pre-restore safety snapshots deliberately defer retention. This prevents a newly created safety snapshot from immediately pruning the snapshot you are trying to restore. The next scheduled backup or explicit `maintenance` run applies the configured policy.

Restic deduplicates unchanged content between snapshots.

## Everyday backup commands

```bash
bash scripts/backup.sh status
bash scripts/backup.sh now
bash scripts/backup.sh list
bash scripts/backup.sh state
bash scripts/backup.sh inspect SNAPSHOT
bash scripts/backup.sh stage SNAPSHOT
bash scripts/backup.sh restore SNAPSHOT
bash scripts/backup.sh check
bash scripts/backup.sh maintenance
bash scripts/backup.sh logs
```

### `status`

Shows backup services and recent backup logs.

### `now`

Creates a consistent manual recovery point:

```text
wait for an in-progress ComfyUI startup
        ↓
stop ComfyUI if running
        ↓
create encrypted snapshot
        ↓
restart ComfyUI if it was running before
```

Retention is deferred for this manual snapshot.

### `list`

Lists available snapshots and IDs:

```bash
bash scripts/backup.sh list
```

### `state`

Pretty-prints `backups/state/current.json`, the manifest captured immediately
before the most recent successful or attempted snapshot:

```bash
bash scripts/backup.sh state
```

### `inspect`

Lists files in a snapshot without restoring them:

```bash
bash scripts/backup.sh inspect a1b2c3d4
bash scripts/backup.sh inspect a1b2c3d4 /source/workflows
```

### `stage`

Extracts a snapshot under the backup tree for manual inspection without changing the live deployment:

```bash
bash scripts/backup.sh stage a1b2c3d4
```

Staged restores are plaintext. Delete them when they are no longer needed.

## Live rollback with `backup.sh restore`

Use this to roll back an existing live deployment:

```bash
bash scripts/backup.sh restore a1b2c3d4
```

Or restore the newest snapshot:

```bash
bash scripts/backup.sh restore latest
```

The helper:

1. stages the selected snapshot,
2. stops ComfyUI when it is running,
3. creates a pre-restore safety snapshot,
4. stops the automatic backup scheduler,
5. restores the backed-up local `.env` configuration,
6. restores each backed-up live data category,
7. applies explicitly selected include paths,
8. restores the Python volume when it was included,
9. rebuilds the current Git checkout when configuration was restored,
10. recreates and starts ComfyUI,
11. restarts the backup service.

Tracked Dockerfiles, Compose files, scripts, and documentation are **not** overwritten by older copies from restic during live rollback. The current Git checkout is authoritative for tracked source. The snapshot may still contain tracked files for staging, inspection, and disaster-recovery evidence.

A complete backed-up category can replace its live tree. If the snapshot used an exclusion pattern or file-size ceiling, the category is overlaid instead so unbacked live files survive.

Categories that were not included are left untouched. With the normal defaults, models, input/output images, and the Python volume are not rolled back.

If restore fails before live files change, the helper attempts to restart the previous services. If it fails after live changes begin, services are left stopped rather than starting a potentially partial deployment. The pre-restore safety snapshot remains available.

### Does live restore rebuild?

When the configuration category is present, yes. The helper rebuilds the **current tracked Git checkout** using the restored local `.env` and then recreates ComfyUI.

Restoring only workflows, user state, images, or models does not inherently require an image rebuild.

## Fresh-clone disaster recovery with `recover.sh`

Use `scripts/recover.sh` when reconstructing a new clone from an existing built-in restic repository.

This is different from live rollback:

- `backup.sh restore` changes an existing deployment in place.
- `recover.sh` starts from a freshly initialized clone and reconstructs the portable application state while preserving the recovery host's local hardware and path choices.

### Prepare the fresh clone

1. Clone ComfierUI.
2. Run normal initialization so the recovery host detects its own hardware, UID/GID, paths, and defaults.
3. Point `COMFYUI_BACKUP_PATH` at the existing backup directory or otherwise make the restic repository and password available there.
4. Ensure `compose.backup.yaml` is in `COMPOSE_FILE`.
5. Start only the backup service.
6. List snapshots and choose an explicit known-good snapshot ID.

Example:

```bash
bash scripts/init.sh
$EDITOR .env

docker compose up -d backup
bash scripts/backup.sh list
bash scripts/recover.sh a1b2c3d4
```

Do not start ComfyUI before running fresh-clone recovery. The recovery helper refuses to run while the clone's ComfyUI service is already running.

### What fresh recovery preserves from the new host

Fresh recovery keeps host-local choices such as:

- Compose project identity,
- bind address and port,
- GPU UUID and accelerator selection,
- UID/GID and supplementary group IDs,
- host paths,
- external network configuration,
- backup destination policy.

These values should describe the recovery host, not blindly reproduce the old machine.

### What fresh recovery reconstructs from the snapshot

Portable application state comes from the recovery blueprint and payload:

- recorded ComfierUI repository commit,
- exact ComfyUI commit when available,
- requested PyTorch, TorchVision, and TorchAudio versions,
- Manager behavior settings,
- custom-node code,
- ComfyUI and Manager user state,
- workflows,
- backed-up optional categories and selective paths.

The repository commit recorded in the snapshot is authoritative for tracked project source. Recovery checks out that commit in detached-HEAD mode rather than copying old tracked files out of restic.

The exact ComfyUI commit is preferred over a floating tag when it was recorded by the running container.

### Python behavior during fresh recovery

When a complete Python-volume payload exists and the snapshot accelerator matches the recovery host accelerator, recovery can reuse that volume.

Otherwise ComfierUI rebuilds the pinned core environment, restores custom nodes, installs discovered custom-node `requirements.txt` files, runs `pip check`, and compares the recovered package set with the blueprint inventory.

Selective individual paths inside the Python volume are not automatically applied during fresh recovery. The core environment is rebuilt and inventory differences are reported instead.

### Models and other excluded assets

Fresh recovery reports how many inventoried model files are present on the recovery host and lists a sample of missing files. It does not pretend excluded assets were restored or automatically redownload models.

Back up rare or irreplaceable models directly when they must be recoverable from restic.

### Recovery image isolation

A fresh recovery build should not overwrite a generic local image tag that may belong to another running deployment on the same Docker host.

If `COMFYUI_IMAGE_TAG` is unset, `recover.sh` automatically assigns a recovery-specific tag derived from the Compose project name before building. An explicitly configured image tag is preserved.

## Automatic backup consistency

The scheduled backup sidecar performs a live backup. It intentionally has no Docker socket and cannot stop ComfyUI.

Both scheduled and manual built-in backups run the same mandatory state capture
as part of the backup operation. There is no separate state timer and no growing
set of local dated manifests. Restic retains historical `current.json` versions
inside its snapshots.

Avoid installing or updating custom nodes during a known automatic backup run when you want the cleanest possible consistency boundary.

For an especially important known-good point, use:

```bash
bash scripts/backup.sh now
```

The manual command stops ComfyUI during the snapshot and restarts it afterward.

## Extra and legacy model libraries

The read-only `COMFYUI_EXTRA_MODELS_PATH` library is inventoried automatically when configured but is not included in the payload unless enabled:

```dotenv
COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS=true
```

The normal writable model tree works the same way:

```dotenv
COMFYUI_BACKUP_INCLUDE_MODELS=true
```

Use fine-grained includes when only selected rare/private model files need protection, or enable a broad model category and exclude known replaceable trees.

## Sensitive workflows and images

Treat the encrypted backup as sensitive.

Workflow JSON can retain API keys, access tokens, URLs, or credentials entered into downloader and API nodes. Generated images can embed workflow metadata and repeat the same values.

Anyone with both the restic repository and its password can recover the protected content. Staged restores are plaintext.

## Advanced: external and host-managed backups

The built-in backup is intended to be a low-friction default. Power users may instead or additionally use:

- another physical disk,
- a NAS or another system,
- an offsite restic backend,
- SFTP, REST server, S3-compatible storage, B2, or another restic-supported backend,
- host-managed restic with a strict ComfyUI stop/start boundary,
- systemd or an existing central backup framework.

The repository retains host-oriented examples:

```text
config/restic.env.example
config/restic-excludes.txt.example
scripts/restic-backup.sh
scripts/restic-maintenance.sh
scripts/restic-restore.sh
systemd/*.example
```

These are advanced integration examples, not requirements for the built-in backup service.

`scripts/restic-backup.sh` also creates an atomic state manifest before invoking
host restic, so the advanced path retains the same node-pack identity evidence.

External backup configuration, credentials, password files, repositories, and restored data should remain outside Git.

## Proof of recovery

A backup is not proven until recovery is tested. Periodically verify:

- the backup service is producing snapshots,
- `bash scripts/backup.sh check` succeeds,
- staging contains representative workflow and user-state files,
- a real live rollback can restore a known-good deployment,
- fresh-clone `scripts/recover.sh` can reconstruct a known-good installation,
- the recovered ComfyUI commit and core runtime match the recovery blueprint,
- restored custom-node dependencies pass `pip check`,
- the blueprint accurately reports deliberately excluded model and environment state,
- irreplaceable assets excluded from restic are recoverable from an independent backup.

The built-in backup is a recovery layer, not a substitute for an independent backup of data you cannot afford to lose.
