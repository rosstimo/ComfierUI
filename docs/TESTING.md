# Testing

## Static validation

```bash
bash scripts/validate.sh
```

This checks Bash syntax, Python compilation, and resolved base, NVIDIA, CPU,
external-network, extra-model, and built-in backup Compose configurations.
Supplemental non-Compose YAML is parsed when PyYAML is available. GitHub Actions
repeats the static validation on pushes and pull requests.

## Fresh-install test

Use a new clone or disposable worktree:

```bash
bash scripts/init.sh
bash scripts/preflight.sh
docker compose build --pull comfyui
docker compose up -d
```

Verify health, a core workflow, output saving, restart, and removal/recreation of
the container without losing state.

## Existing-library test

Point models and workflows at existing absolute paths. Verify:

- parent traversal,
- supplementary group access,
- model listing,
- model reads during generation,
- optional model writes or downloads,
- newly created file ownership and group inheritance.

## Custom-node test

Install one registered node pack, restart, and prove:

- code exists in the bind mount,
- Python dependencies exist in the named volume,
- import logs are clean,
- dependencies survive an image rebuild,
- the selected workflow runs.

## Accelerator test

Record the selected profile, GPU, compute capability, driver, CUDA image,
PyTorch build, and actual inference result. CPU and CUDA 12 compatibility modes
must be tested separately from the CUDA 13 path.

## Network test

When using the external-network override, verify DNS from a peer container:

```bash
docker run --rm --network ai-services curlimages/curl \
  -fsS http://comfyui:8188/system_stats
```

## Backup and live-rollback test

Use a disposable deployment with representative custom nodes, user state,
workflows, models, and image files.

Verify:

- automatic encrypted backup creation,
- manual consistent snapshots,
- snapshot listing and inspection,
- retention grouped by host and tag,
- manual and pre-restore safety snapshots defer retention,
- complete categories replace their live tree,
- partial/selective categories overlay without deleting unbacked siblings,
- excluded categories remain untouched,
- tracked repository files remain Git-authoritative,
- local `.env` recovery works,
- ComfyUI returns healthy after rollback,
- the backup service restarts after rollback.

Test at least one fine-grained include and one exclusion or size policy.

## Fresh-clone recovery test

Use a completely new clone with a different Compose project name and port.
Initialize it normally, point it at an existing built-in restic repository, start
only the backup service, then recover an explicit known-good snapshot:

```bash
bash scripts/init.sh
$EDITOR .env

docker compose up -d backup
bash scripts/backup.sh list
bash scripts/recover.sh SNAPSHOT
```

Verify:

- host-local project name, port, GPU selection, paths, and backup location survive,
- the recorded ComfierUI repository commit is checked out,
- the exact recorded ComfyUI commit is rebuilt,
- requested PyTorch-family build pins are reproduced,
- custom nodes are restored,
- workflows and user/Manager state are restored,
- custom-node requirements are reconciled,
- `pip check` reports no broken requirements,
- ComfyUI becomes healthy,
- deliberately excluded models are reported rather than falsely claimed restored,
- the recovery image uses an isolated tag and does not replace another deployment's local image tag.

A staged snapshot is plaintext. Remove staged recovery-test data after validation.

## Recovery evidence validated on the reference NVIDIA host

The recovery implementation has been exercised end to end on the verified CUDA 13
host with an RTX 4060 Ti, including:

- encrypted restic snapshots and retention behavior,
- live same-host rollback,
- recovery blueprint/runtime-state capture,
- selective restore behavior,
- fresh-clone reconstruction,
- exact ComfyUI commit recovery,
- PyTorch 2.11.0 CUDA 13 core reconstruction,
- custom-node dependency reconciliation,
- healthy startup with restored custom nodes and workflows,
- clean `pip check`,
- recovery image-tag isolation.

This evidence validates the recovery workflow on the verified NVIDIA profile. It
does not promote CUDA 12 or CPU profiles beyond their separately documented
experimental status.

## Release evidence

Attach command output or concise notes to the release or pull request. A checked
box without evidence is easy to misunderstand later.
