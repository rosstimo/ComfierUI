# Backup and restore

ComfierUI includes a simple built-in backup service for normal users and keeps
external/host-managed backup workflows as an advanced option.

## Built-in automatic backups

The normal backup path is fully Docker Compose managed:

- no host restic installation,
- no host cron job,
- no systemd timer,
- no Docker socket mounted into the backup container.

The backup service uses restic and writes an encrypted repository to the
host-visible backup directory. The default is:

```text
./backups/
├── restic/           # encrypted restic repository
├── restore/          # optional staged snapshot copies
├── state/            # scheduler state and recovery manifest
└── restic-password   # generated repository password
```

The entire `./backups/` tree is ignored by Git.

## Enable backups

For a fresh installation, the backup sidecar is already present in the normal
Compose stack but remains idle. Enable it in `.env`:

```dotenv
COMFYUI_BACKUP_ENABLED=true
```

Then recreate the services:

```bash
docker compose up -d --force-recreate
```

For an existing installation created before the backup sidecar was added, append
`compose.backup.yaml` to `COMPOSE_FILE` once:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.extra-models.yaml:compose.backup.yaml
COMFYUI_BACKUP_ENABLED=true
```

Keep only the Compose layers your deployment actually uses. `scripts/init.sh`
preserves optional layers already present in `COMPOSE_FILE`.

On startup, a small one-shot helper creates the backup directory with the
configured `PUID`/`PGID`. The backup service then:

1. generates `restic-password` if one does not exist,
2. initializes the encrypted restic repository if needed,
3. waits for the configured initial delay,
4. creates backups automatically on the configured interval,
5. applies retention after each successful backup.

Copy the generated password file to a separate safe location. Losing both the
backup directory and its password means the repository cannot be recovered.

## Easy backup configuration

The defaults are intended to be understandable without knowing restic.

```dotenv
# Turn automatic backups on or off.
COMFYUI_BACKUP_ENABLED=true

# Local host-visible backup directory.
COMFYUI_BACKUP_PATH=./backups

# Schedule: first backup after 10 minutes, then every 24 hours.
COMFYUI_BACKUP_START_DELAY_MINUTES=10
COMFYUI_BACKUP_INTERVAL_HOURS=24

# Retry a failed backup after one hour.
COMFYUI_BACKUP_RETRY_MINUTES=60
```

## What is included

Essential recovery state is included by default:

```dotenv
COMFYUI_BACKUP_INCLUDE_CONFIG=true
COMFYUI_BACKUP_INCLUDE_CUSTOM_NODES=true
COMFYUI_BACKUP_INCLUDE_USER=true
COMFYUI_BACKUP_INCLUDE_WORKFLOWS=true
```

Potentially large or separately managed data is excluded by default:

```dotenv
COMFYUI_BACKUP_INCLUDE_INPUT=false
COMFYUI_BACKUP_INCLUDE_OUTPUT=false
COMFYUI_BACKUP_INCLUDE_MODELS=false
COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS=false
COMFYUI_BACKUP_INCLUDE_PYTHON=false
```

Set an `INCLUDE_*` option to `true` to include that category, or `false` to
exclude it.

The default configuration backup includes `.env`, the Dockerfile, Docker support
files, and the active repository-local Compose layers. User state includes
ComfyUI and Manager state. Workflows are backed up from
`COMFYUI_WORKFLOWS_PATH`, including an external workflow directory.

Inputs and outputs default to off because image collections can be large and
ComfyUI-generated images may embed workflow metadata. Models default to off
because they frequently dominate backup size. The Python volume defaults to off
because it can be large and is often reusable across normal rollbacks.

A restore can only restore categories present in that snapshot. Categories that
were excluded are left untouched in the live deployment. For example, the
normal default restore rolls back configuration, custom nodes, user/Manager
state, and workflows while leaving the current model and image libraries in
place.

For the closest possible dependency rollback, enable:

```dotenv
COMFYUI_BACKUP_INCLUDE_PYTHON=true
```

Without that option, a live restore leaves the current persistent Python volume
in place. This is usually convenient, but it is not a bit-for-bit rollback of
custom-node Python package versions.

## Sensitive workflows and images

Treat the encrypted backup as sensitive.

Workflow JSON can retain API keys, access tokens, URLs, or credentials entered
into downloader and API nodes. Generated images can embed workflow metadata and
therefore repeat the same sensitive values. Civitai, Hugging Face, and similar
credential-bearing downloader workflows are examples of why this matters.

The backup repository is encrypted, but anyone with both the repository and its
password can recover this data.

## Retention defaults

The novice defaults keep several recent recovery points plus longer history:

```dotenv
COMFYUI_BACKUP_KEEP_LAST=3
COMFYUI_BACKUP_KEEP_DAILY=7
COMFYUI_BACKUP_KEEP_WEEKLY=4
COMFYUI_BACKUP_KEEP_MONTHLY=12
COMFYUI_BACKUP_KEEP_YEARLY=3
```

After each successful backup, restic applies these rules and prunes unneeded
repository data. Unchanged content is deduplicated between snapshots.

These values are intentionally conservative for the default small backup set.
Including large model, image, or Python trees increases the initial repository
size, although unchanged content is deduplicated in later snapshots.

## Everyday backup commands

Normal users can manage the built-in backup with one helper:

```bash
bash scripts/backup.sh status
bash scripts/backup.sh now
bash scripts/backup.sh list
bash scripts/backup.sh inspect SNAPSHOT
bash scripts/backup.sh stage SNAPSHOT
bash scripts/backup.sh restore SNAPSHOT
bash scripts/backup.sh check
bash scripts/backup.sh maintenance
bash scripts/backup.sh logs
```

### `status`

Shows the backup services and recent backup logs. It does not stop ComfyUI.

### `now`

Creates a consistent manual snapshot:

```text
stop ComfyUI if running
        ↓
run backup
        ↓
restart ComfyUI if it was running before
```

The automatic schedule is not changed.

### `list`

Lists available encrypted restic snapshots and their IDs:

```bash
bash scripts/backup.sh list
```

This is the normal starting point when choosing a recovery point.

### `inspect`

Lists files inside a snapshot without restoring anything:

```bash
bash scripts/backup.sh inspect a1b2c3d4
```

To narrow inspection to one path:

```bash
bash scripts/backup.sh inspect a1b2c3d4 /source/workflows
```

### `stage`

`stage` is optional. It extracts a snapshot under `./backups/restore/` for manual
inspection without changing the live deployment:

```bash
bash scripts/backup.sh stage a1b2c3d4
```

Use this when you want to browse or manually recover individual files. Most users
who simply want to roll back ComfierUI do not need to stage first.

### `restore`

`restore` means an actual functional rollback of the live deployment:

```bash
bash scripts/backup.sh restore a1b2c3d4
```

Or restore the newest snapshot:

```bash
bash scripts/backup.sh restore latest
```

The helper performs the recovery workflow automatically:

1. extracts the selected snapshot to a private staging directory,
2. stops ComfyUI,
3. creates a pre-restore safety snapshot of the current state,
4. stops the automatic backup scheduler,
5. restores deployment configuration included in the snapshot,
6. restores each backed-up live data category,
7. rebuilds the ComfyUI image when deployment configuration was restored,
8. restores the Python volume when it was included in the snapshot,
9. recreates and starts ComfyUI,
10. restarts the backup service.

If a category was not included in the selected snapshot, `restore` leaves the
current live category untouched rather than deleting it.

The pre-restore safety snapshot gives you a recovery point for the state that
existed immediately before rollback.

If the restore fails before live files are changed, the helper restarts the
previous services. If it fails after live files begin changing, services are
left stopped instead of starting a potentially partial restore. The safety
snapshot remains available for recovery.

## Does restore rebuild?

With the normal defaults, yes. Configuration is included by default, so the live
restore rebuilds the ComfyUI image from the restored Dockerfile and deployment
configuration, then recreates the container.

A snapshot that does not contain deployment configuration can be restored
without rebuilding. The helper detects this automatically.

Restoring workflows, user state, images, or models by themselves does not require
an image rebuild. Restoring `.env`, Dockerfile, Docker support files, or active
Compose configuration does.

## Automatic backup consistency

The scheduled backup sidecar performs a live backup. It intentionally has no
Docker socket and cannot stop or restart ComfyUI.

For the default source set this is a useful low-hassle recovery mechanism, but a
backup taken while Manager is actively changing custom-node repositories or user
state can capture files at slightly different moments. Avoid installing or
updating custom nodes during a known automatic backup run.

For an especially important known-good recovery point, use:

```bash
bash scripts/backup.sh now
```

The manual command stops ComfyUI during the snapshot and starts it again when the
backup finishes.

## Extra/legacy model library backup

The read-only `COMFYUI_EXTRA_MODELS_PATH` library is intentionally excluded from
the normal built-in backup. It is commonly very large and may already be managed
independently.

To opt in, add the extra-model backup mount layer and enable the category:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.extra-models.yaml:compose.backup.yaml:compose.backup-extra-models.yaml
COMFYUI_BACKUP_INCLUDE_EXTRA_MODELS=true
```

Use only the accelerator and optional layers appropriate for your deployment.

## Advanced: external and host-managed backups

The built-in backup is designed for low-friction local recovery. A repository on
the same disk does not protect against disk failure, theft, fire, or loss of the
whole machine.

Power users should consider one or more of:

- a backup directory on another physical disk,
- a NAS or another system,
- an offsite restic backend,
- SFTP, REST server, S3-compatible storage, B2, or another supported backend,
- host-managed restic with a strict ComfyUI stop/start boundary,
- systemd or an existing central backup framework.

The repository includes host-oriented examples:

```text
config/restic.env.example
config/restic-excludes.txt.example
scripts/restic-backup.sh
scripts/restic-maintenance.sh
scripts/restic-restore.sh
systemd/*.example
```

Those are advanced integration examples, not requirements for the built-in
backup service.

For an established host restic framework, point the scripts at external config:

```bash
RESTIC_ENV_FILE=/secure/path/comfierui-restic.env \
RESTIC_EXCLUDE_FILE=/secure/path/comfierui-excludes.txt \
  bash scripts/restic-backup.sh
```

External backup configuration, credentials, password files, repositories, and
restored data should remain outside Git.

## Proof of recovery

A backup is not proven until a restore succeeds. Periodically verify:

- the backup service is running and producing snapshots,
- `bash scripts/backup.sh check` succeeds,
- `bash scripts/backup.sh stage SNAPSHOT` contains a known workflow and user-state file,
- a real `bash scripts/backup.sh restore SNAPSHOT` can reconstruct a known working deployment,
- any separately managed models or outputs are recoverable from their own backup.

The built-in backup is a convenience recovery layer. Important installations
should still maintain at least one independent copy on another disk, system, or
offsite destination.
