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
├── restore/          # staged restores
├── state/            # scheduler state and recovery manifest
└── restic-password   # generated repository password
```

The entire `./backups/` tree is ignored by Git.

### Enable backups

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

The first generated password is shown in the backup logs. Copy the password file
to a separate safe location. Losing both the backup directory and its password
means the repository cannot be recovered.

### Easy backup configuration

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

### What is included

Essential recovery state is included by default:

```dotenv
COMFYUI_BACKUP_INCLUDE_CONFIG=true
COMFYUI_BACKUP_INCLUDE_CUSTOM_NODES=true
COMFYUI_BACKUP_INCLUDE_USER=true
COMFYUI_BACKUP_INCLUDE_WORKFLOWS=true
```

Potentially large or easily reproduced data is excluded by default:

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
because rebuilding dependencies cleanly is often safer than restoring an old
virtual environment.

### Sensitive workflows and images

Treat the encrypted backup as sensitive.

Workflow JSON can retain API keys, access tokens, URLs, or credentials entered
into downloader and API nodes. Generated images can embed workflow metadata and
therefore repeat the same sensitive values. Civitai, Hugging Face, and similar
credential-bearing downloader workflows are examples of why this matters.

The backup repository is encrypted, but anyone with both the repository and its
password can recover this data.

### Retention defaults

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
Including large model or image trees can make pruning take substantially longer.

### Status and manual backup

Check the service:

```bash
docker compose ps backup backup-init
docker compose logs --tail=100 backup
```

Run an immediate backup without changing the automatic schedule:

```bash
docker compose exec backup \
  /bin/sh /usr/local/bin/comfierui-backup backup-now
```

List snapshots:

```bash
docker compose exec backup \
  /bin/sh /usr/local/bin/comfierui-backup snapshots
```

Verify the repository structure:

```bash
docker compose exec backup \
  /bin/sh /usr/local/bin/comfierui-backup check
```

## Safe staged restore

The built-in restore command never writes directly into the live ComfyUI data.
It restores to a new staging directory under `/backups/restore`.

Restore the latest ComfierUI snapshot:

```bash
docker compose exec backup \
  /bin/sh /usr/local/bin/comfierui-backup restore latest
```

The command prints the container path, for example:

```text
/backups/restore/20260718T120000Z
```

With the default backup path, the same files are visible on the host at:

```text
./backups/restore/20260718T120000Z
```

The restore command refuses to use a non-empty staging directory. Inspect the
restored files before deliberately copying anything into the live deployment.

A normal recovery order is:

1. inspect the staged recovery manifest,
2. restore `.env` and local Compose configuration,
3. restore custom nodes and user/Manager state,
4. restore workflows,
5. restore optional inputs, outputs, or models only when they were backed up,
6. rebuild/recreate ComfierUI,
7. run permission checks,
8. run a representative workflow.

## Consistency limits of the simple automatic backup

The built-in sidecar performs a live backup. It intentionally has no Docker
socket and cannot stop or restart ComfyUI.

For the default source set this is a useful low-hassle recovery mechanism, but a
backup taken while Manager is actively changing custom-node repositories or user
state can capture files at slightly different moments. Avoid installing or
updating custom nodes during a known backup run. An immediate manual backup after
a successful major configuration change is also reasonable.

Users who require a strict stop-backup-start consistency boundary should use the
advanced external workflow below.

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

The repository still includes the earlier host-oriented examples:

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
- `restic check` succeeds,
- a staged restore contains a known workflow and user-state file,
- a representative restored configuration can reconstruct the deployment,
- any separately managed models or outputs are recoverable from their own backup.

The built-in backup is a convenience recovery layer. Important installations
should still maintain at least one independent copy on another disk, system, or
offsite destination.
