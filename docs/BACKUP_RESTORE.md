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

Outputs, models, and the Python volume are disabled independently. The
configurable `RESTIC_TAG` isolates this deployment inside a shared restic
repository. `restore latest` resolves the newest snapshot carrying that tag before
restoring. The restic environment and password file are not included in their
own backup source set.

## Consistency

Stopping ComfyUI gives the clearest consistency boundary while Manager may
change node repositories or Python packages. The default script stops the
service only when it is running and starts it again during cleanup.

```bash
bash scripts/restic-backup.sh
```

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

Inspect paths and ownership. A deliberate recovery normally follows this order:

1. Clone or restore the repository configuration.
2. Restore `.env` and local overrides.
3. Restore workflows, user state, and custom nodes.
4. Restore selected input/output assets.
5. Restore models only when they were included.
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
- one workflow and user-state file,
- a representative private model or output when included,
- a staged Python-volume recovery when enabled,
- full deployment reconstruction on another directory or host.

Official restic documentation: https://restic.readthedocs.io/
