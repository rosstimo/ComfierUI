# Testing

## Static validation

```bash
bash scripts/validate.sh
```

This checks Bash syntax, Python compilation, and resolved base, NVIDIA, CPU,
external-network, and extra-model-path Compose configurations. Supplemental
non-Compose YAML is parsed when PyYAML is available; CI installs it explicitly. GitHub Actions repeats this
check on pushes and pull requests.

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

## Backup test

Perform a real restic backup, restore into staging, compare representative files,
and reconstruct the service without modifying the live deployment.

## Release evidence

Attach command output or concise notes to the release or pull request. A checked
box without evidence is easy to misunderstand later.
