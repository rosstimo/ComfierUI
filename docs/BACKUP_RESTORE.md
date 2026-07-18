# Backup and restore

## Recovery model

Classify state before deciding backup size:

- **Deployment configuration:** `.env`, active Compose overrides, Dockerfile,
  entrypoint, and optional extra-model configuration.
- **Irreplaceable state:** workflows, user settings, Manager state, prompts,
  private scripts, and selected input/output assets.
- **Installed extensions:** custom-node repositories and optionally the Python
  named volume.
- **Large reproducible state:** downloaded models and caches.

Caches and temp files are excluded. Models are opt-in because they often dominate
repository size, but private, modified, deleted, or difficult-to-source models
are critical data and should be enabled.

ComfyUI workflows and generated images can contain sensitive metadata. Workflow
JSON may retain credentials entered into downloader or API nodes, and images can
embed workflow data, prompts, filenames, URLs, or the same credentials. Restic
encrypts repository contents, but the repository credentials and restored files
must still be treated as sensitive.

## Restic setup

Keep the restic password file outside this repository and outside the only backup
it unlocks. Store a recovery copy through a separate secure channel.

```bash
cp config/restic.env.example config/restic.env
cp config/restic-excludes.txt.example config/restic-excludes.txt
chmod 600 config/restic.env
$EDITOR config/restic.env
restic init                 # only for a new repository
restic snapshots --tag comfierui
```

For a pre-existing restic framework, leave the local example untouched and point
the script at the established configuration:

```bash
RESTIC_ENV_FILE=/secure/path/comfierui-restic.env \
RESTIC_EXCLUDE_FILE=/secure/path/comfierui-excludes.txt \
  bash scripts/restic-backup.sh
```

The backup script verifies that the configured restic repository can be opened
before stopping ComfyUI. A wrong password, unreachable backend, or uninitialized
repository therefore fails before service interruption.

## Default source set

Every snapshot also includes a generated recovery manifest with the deployment
Git commit, active Compose layers, container/image identity, and built ComfyUI
commit. It intentionally omits environment values and credentials.

The example includes:

- `.env`, Dockerfile, entrypoint, and every active file named in `COMPOSE_FILE`,
- custom nodes,
- user and Manager state,
- workflows,
- inputs.

Outputs, models, the separate extra/legacy model library, and the Python volume
are disabled independently. The configurable `RESTIC_TAG` isolates this
deployment inside a shared restic repository. `restore latest` resolves the
newest snapshot carrying that tag before restoring. The restic environment and
password file are not included in their own backup source set.

### Model backup choices

The two model libraries are independent:

```dotenv
# Writable model tree used for new Manager downloads
COMFYUI_BACKUP_MODELS=false

# Optional read-only legacy/shared library from COMFYUI_EXTRA_MODELS_PATH
COMFYUI_BACKUP_EXTRA_MODELS=false
```

Enable either only when that content is not adequately protected elsewhere.
Large model libraries can make backup, prune, repository checks, and restore
tests substantially more expensive.

Setting `COMFYUI_BACKUP_EXTRA_MODELS=true` requires
`COMFYUI_EXTRA_MODELS_PATH` to be configured in `.env`. The backup reads the
host library directly; the read-only container mount does not prevent restic
from backing it up.

## Consistency

Stopping ComfyUI gives the clearest consistency boundary while Manager may
change node repositories or Python packages. The default script stops the
service only when it is running and starts it again during cleanup, including
when the backup fails after service shutdown.

```bash
bash scripts/restic-backup.sh
```

The script prints the exact source paths before starting the snapshot. Review
that list, especially when workflows or model libraries use absolute external
paths.

After the first run:

```bash
restic snapshots --tag comfierui
# Choose a listed snapshot ID for direct inspection.
restic ls SNAPSHOT_ID
```

## Retention and repository checks

Backup, retention, and deep verification are separate jobs:

```bash
bash scripts/restic-maintenance.sh regular
bash scripts/restic-maintenance.sh deep
```

The regular job forgets snapshots according to policy, prunes, and reads a
subset of repository data. The deep job reads all repository data. Schedule the
deep read less often when the repository is large.

## Systemd examples

Copy the six `.example` units, replace `CHANGE_ME` and `/path/to/ComfierUI`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now comfierui-backup.timer
sudo systemctl enable --now comfierui-maintenance.timer
sudo systemctl enable --now comfierui-deep-check.timer
systemctl list-timers 'comfierui-*'
```

Keep the timer files under version control only as templates. Installed units are
host policy and may use a central backup path rather than repository-local files.

## Restore to staging

Never test a restore by overwriting the only live copy:

```bash
bash scripts/restic-restore.sh latest /tmp/comfierui-restore
```

The target must be new or empty. The script refuses a non-empty staging directory
so an old restore cannot silently mix with the snapshot being tested.

Restic preserves source paths beneath the staging target. For example, an
external workflow directory at `/mnt/nvme2/ComfierUI/workflows` will normally
appear beneath a path similar to:

```text
/tmp/comfierui-restore/mnt/nvme2/ComfierUI/workflows
```

Inspect paths, ownership, workflow contents, and image metadata before copying
anything into the live deployment.

A deliberate recovery normally follows this order:

1. Clone or restore the repository configuration.
2. Restore `.env` and local overrides.
3. Restore workflows, user state, and custom nodes.
4. Restore selected input/output assets.
5. Restore the writable and/or extra model libraries only when they were included.
6. Rebuild the image.
7. Reinstall dependencies cleanly or restore the Python volume.
8. Run permission checks and representative workflows.

## Restoring the Python volume

Only restore a venv tar into an empty, stopped volume. Confirm every path first:

```bash
docker compose down
volume="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-comfierui}" \
  --filter 'label=com.docker.compose.volume=comfyui-python' | head -n1)"

docker run --rm \
  -v "${volume}:/volume" \
  -v /tmp/comfierui-restore/path/to/staging:/restore:ro \
  alpine:3.22 sh -c \
  'rm -rf /volume/* /volume/.[!.]* /volume/..?*; tar -C /volume -xf /restore/comfyui-python.tar'
```

A clean venv rebuild is often safer than restoring old dependency conflicts.

## Proof of recovery

A backup is not proven until a restore test succeeds. Periodically verify:

- restic repository checks,
- the recovery manifest,
- one workflow and user-state file,
- a representative input/output asset when included,
- a representative local or extra model when either model option is enabled,
- a staged Python-volume recovery when enabled,
- full deployment reconstruction on another directory or host.

A practical first test is:

```bash
bash scripts/restic-backup.sh
rm -rf /tmp/comfierui-restore-test
bash scripts/restic-restore.sh latest /tmp/comfierui-restore-test
find /tmp/comfierui-restore-test -name recovery-manifest.txt -print
```

Then compare representative restored files with their live sources before testing
any destructive recovery procedure.

Official restic documentation: https://restic.readthedocs.io/
